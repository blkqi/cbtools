import os
import shutil
import string
import unicodedata

from pathlib import Path
from operator import itemgetter
from itertools import chain

from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.functools import unique, not_unique, compose, normalizable


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


def _name_from_info(cinfo):
    template = string.Template(config['rename.pattern'])
    segments = _format_segments(cinfo)
    return template.substitute(**segments).strip()


def _construct_rename_pairs(paths, *, root):
    for src in paths:
        cinfo = ComicArchive(src).info()
        if cinfo:
            dst = Path(root) / (_name_from_info(cinfo) + src.suffix)
            if src != dst:
                yield src, dst
            else:
                logger.debug(f'Source {src} and destination are the same - skipping rename')
        else:
            logger.warning(f'File "{src}" contains no info xml - skipping rename')


def _construct_rename_extra(parents):
    for inc in config['rename.move_includes']:
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


def _check_errors(pairs):
    def log_missing(src):
        logger.error(f'Source {src} doesn\'t exist!')
        return src

    def log_replace(dst):
        logger.error(f'Destination {dst} already exists!')
        return dst

    def log_collide(dst):
        logger.error(f'More than one file would be renamed to {dst}!')
        return dst

    gen_missing = unique(sorted(src for src, _ in pairs if not src.exists()))
    gen_replace = unique(sorted(dst for _, dst in pairs if dst.exists()))
    gen_collide = not_unique(sorted(dst for _, dst in pairs))

    errors = chain(map(log_missing, gen_missing),
                   map(log_replace, gen_replace),
                   map(log_collide, gen_collide))

    return any(list(errors))


def rename(files, root=Path(''), dryrun=False):
    paths = expand_paths(files)
    pairs = set(_construct_rename_pairs(paths, root=root))

    if _check_errors(pairs) and not dryrun:
        return

    parents = set((src.parent, dst.parent) for src, dst in pairs if src.parent != dst.parent)
    extra = set(_construct_rename_extra(parents))
    union = pairs.union(extra)

    for src, dst in sorted(union, key=itemgetter(1)):
        if dryrun:
            print(f'(dryrun) "{src}" -> "{dst}"')
        else:
            _rename_file(src, dst)

    for src, _ in parents:
        try:
            next(f for f in src.iterdir() if not f.name.startswith('.'))
        except StopIteration:
            src.rmdir()
