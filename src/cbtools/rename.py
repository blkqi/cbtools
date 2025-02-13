import os
import shutil
import string

from pathlib import Path
from operator import itemgetter
from itertools import chain

from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.functools import unique, not_unique


def _allowed_chars():
    return string.ascii_letters + string.digits + " _-~.'!@#$%^&()[]{}"


def _sanitize_paths(value):
    return ''.join(c for c in value if c in _allowed_chars())


def _formatters():
    def volume_formatter(volume):
        i, f = str(volume).split('.') if '.' in volume else (str(volume), None)
        return f'V{int(i):02}' + str(f'.{f}' if f else '')

    return (
        ('Series', str),
        ('Writer', str),
        ('Year', str),
        ('Volume', volume_formatter),
    )


def _name_from_info(cinfo, default=''):
    # prefer localized series to series, if it exists
    cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')

    template = string.Template(config['rename.pattern'])
    defaults = {key: default for key in template.get_identifiers()}
    values = {k: _sanitize_paths(f(cinfo[k])) for k, f in _formatters() if k in cinfo}
    strpath = template.substitute(defaults, **values)

    return strpath.strip()


def _construct_rename_pairs(paths, *, root):
    for src in paths:
        cinfo = ComicArchive(src).info()
        if cinfo:
            dst = (root / _name_from_info(cinfo)).with_suffix(src.suffix)
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


def _check_has_errors(pairs):
    log_noexist = lambda src: logger.error(f'Source {src} doesn\'t exist!') or src
    log_replace = lambda dst: logger.error(f'Destination {dst} already exists!') or dst
    log_collide = lambda dst: logger.error(f'More than one file would be renamed to {dst}!') or dst

    gen_noexist = unique(sorted(src for src, _ in pairs if not src.exists()))
    gen_replace = unique(sorted(dst for _, dst in pairs if dst.exists()))
    gen_collide = not_unique(sorted(dst for _, dst in pairs))

    all_errors = chain(map(log_noexist, gen_noexist),
                       map(log_replace, gen_replace),
                       map(log_collide, gen_collide))

    return any(list(all_errors))


def rename(files, root=Path(''), dryrun=False):
    paths = expand_paths(files)
    pairs = set(_construct_rename_pairs(paths, root=root))

    if _check_has_errors(pairs) and not dryrun:
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
            next(src.iterdir())
        except StopIteration:
            src.rmdir()
