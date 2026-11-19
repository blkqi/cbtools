import shutil
import string
import unicodedata
import errno

from pathlib import Path
from operator import itemgetter
from itertools import chain

from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.functools import unique, not_unique, compose, normalizable
from cbtools.activity import StageIssue, StageResult


_allowed_symbols = " _-~.'!@#$%^&()[]{}"
_unicode_chars = normalizable(range(0x0100, 0x0180))


def _allowed_chars():
    return string.ascii_letters + string.digits + _allowed_symbols + _unicode_chars


def _sanitize_segment(value):
    return ''.join(c for c in unicodedata.normalize(
        'NFD', value) if c in _allowed_chars() and not unicodedata.combining(c))


def _formatters():
    def format_series(cinfo):
        # prefer localized series to series, if it exists
        series = cinfo.get('LocalizedSeries') or cinfo.get('Series')
        return str(series or '')

    def format_writer(cinfo):
        writer = cinfo.get('Writer')
        return str(writer or '')

    def format_year(cinfo):
        year = cinfo.get('Year')
        return str(year or '')

    def format_volume(cinfo):
        volume = cinfo.get('Volume', '')
        i, f = str(volume).split('.') if '.' in volume else (str(volume), None)
        return f'V{int(i):02}' + str(f'.{f}' if f else '')

    formatters = (
        ('Series', format_series),
        ('Writer', format_writer),
        ('Year',   format_year),
        ('Volume', format_volume),
    )
    return ((k, compose(_sanitize_segment, f)) for k, f in formatters)


def _format_segments(cinfo):
    return {key: fun(cinfo) for key, fun in _formatters()}


def _name_from_info(cinfo, settings=None):
    settings = config if settings is None else settings
    template = string.Template(settings['rename.pattern'])
    segments = _format_segments(cinfo)
    return template.substitute(**segments).strip()


def _construct_rename_pairs(paths, *, root, settings=None, issues=None):
    for src in paths:
        cinfo = ComicArchive(src).info()
        if cinfo:
            dst = Path(root) / (_name_from_info(cinfo, settings) + src.suffix)
            if src != dst:
                yield src, dst
            else:
                logger.debug(f'Source {src} and destination are the same - skipping rename')
        else:
            message = f'File "{src}" contains no info xml - skipping rename'
            if issues is None:
                logger.warning(message)
            else:
                issues.append(StageIssue("METADATA_MISSING", message, "Check tagging and rescan the folder."))


def _construct_rename_extra(parents, settings=None, *, preserve_series_id=False):
    settings = config if settings is None else settings
    includes = set(settings['rename.move_includes'])
    if preserve_series_id:
        includes.add(settings['tag.series_id_filename'])
    for inc in includes:
        for src, dst in parents:
            if (src / inc).exists():
                yield (src / inc, dst / inc)


def _rename_file(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        src.rename(dst)
    except OSError as e:
        if e.errno == 18:
            pass
        else:
            raise
    else:
        return

    logger.debug('errno 18: target filesystem differs - fallback to copy file')
    shutil.copyfile(src, dst)
    src.unlink()


def _check_errors(pairs, details=None):
    def record(message):
        if details is None:
            logger.error(message)
        else:
            details.append(message)

    def log_missing(src):
        record(f'Source {src} doesn\'t exist!')
        return src

    def log_replace(dst):
        record(f'Destination {dst} already exists!')
        return dst

    def log_collide(dst):
        record(f'More than one file would be renamed to {dst}!')
        return dst

    def log_reuse(src):
        record(f'Source {src} has more than one destination!')
        return src

    gen_missing = unique(sorted(src for src, _ in pairs if not src.exists()))
    gen_replace = unique(sorted(dst for _, dst in pairs if dst.exists()))
    gen_collide = not_unique(sorted(dst for _, dst in pairs))
    gen_reused = not_unique(sorted(src for src, _ in pairs))

    errors = chain(map(log_missing, gen_missing),
                   map(log_replace, gen_replace),
                   map(log_collide, gen_collide),
                   map(log_reuse, gen_reused))

    return any(list(errors))


def rename(files, root=Path(''), dryrun=False, *, settings=None, reporter=None, enforce_root=False):
    settings = config if settings is None else settings
    paths = list(expand_paths(files))
    issues = [] if reporter is not None else None
    pairs = set(_construct_rename_pairs(paths, root=root, settings=settings, issues=issues))
    if issues:
        return StageResult(failed=len(paths), issues=issues)

    errors = [] if reporter is not None else None
    if _check_errors(pairs, errors) and (not dryrun or reporter is not None):
        if reporter is None:
            return
        return StageResult(failed=len(pairs), issues=[StageIssue(
            "RENAME_CONFLICT", "; ".join(errors)[:1000],
            "Resolve the source/destination naming conflict, then rescan the folder.",
        )])

    parents = set((src.parent, dst.parent) for src, dst in pairs if src.parent != dst.parent)
    extra = set(_construct_rename_extra(parents, settings, preserve_series_id=reporter is not None))
    union = pairs.union(extra)
    if reporter is not None:
        extra_errors = []
        if _check_errors(union, extra_errors):
            return StageResult(failed=len(pairs), issues=[StageIssue(
                "RENAME_CONFLICT", "; ".join(extra_errors)[:1000],
                "Resolve the archive/sidecar destination conflict, then rescan the folder.",
            )])
    root = Path(root).resolve()
    if enforce_root and any(not path.resolve().is_relative_to(root) for pair in union for path in pair):
        return StageResult(failed=len(pairs), issues=[StageIssue(
            "UNSAFE_PATH", "A rename would leave the configured library.",
            "Correct the rename pattern or sidecar paths before rescanning.",
        )])
    if reporter is not None:
        reporter.moving(dst.parent for _, dst in union)

    for src, dst in sorted(union, key=itemgetter(1)):
        if dryrun:
            print(f'(dryrun) "{src}" -> "{dst}"')
        else:
            _rename_file(src, dst)

    result = StageResult(changed=len(pairs), unchanged=len(paths) - len(pairs),
                         outputs=[str(dst) for _, dst in sorted(pairs, key=itemgetter(1))])
    if dryrun:
        return result if reporter is not None else None

    for src, _ in parents:
        if enforce_root and src.resolve() == root:
            continue
        if reporter is not None:
            try:
                src.rmdir()
            except OSError as error:
                if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    logger.warning('Failed to remove empty directory %s: %s', src, error)
            continue
        try:
            next(f for f in src.iterdir() if not f.name.startswith('.'))
        except StopIteration:
            try:
                shutil.rmtree(src)
            except OSError:
                logger.warning(f'Failed to remove directory: {src}')

    return result if reporter is not None else None
