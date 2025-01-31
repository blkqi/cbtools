import os
import shutil
import string
import pathlib
import logging

from cbtools.config import config
from cbtools.core import CBZFile, expand_paths
from operator import itemgetter

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

__allowed_chars = string.ascii_letters + string.digits + " _-~.'!@#$%^&()[]{}"
__formatters = {'Series': str,
                'Volume': lambda x: f'V{int(x.split('.')[0]):02}{f'.{x.split('.')[-1]}' if '.' in x else ''}',
                'Writer': str,
                'Year':   str}

def sanitize_paths(value):
    return ''.join(c for c in value if c in __allowed_chars)

def format_cinfo(cfile, pattern, default=''):
    cinfo = cfile.info

    # prefer localized series to series
    cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')

    template = string.Template(pattern)
    defaults = {key: default for key in template.get_identifiers()}
    values = {k: sanitize_paths(f(cinfo[k])) for k, f in __formatters.items() if k in cinfo}
    strpath = template.substitute(defaults, **values)

    return pathlib.Path(strpath.strip())

def determine_path(cfile, *, root, pattern=config['rename_pattern']):
    stem = format_cinfo(cfile, pattern)
    suffix = cfile.Path().suffix
    return (root / stem).with_suffix(suffix)

def determine_pairs(paths, *, root):
    for src in paths:
        with CBZFile(src) as cfile:
            dst = determine_path(cfile, root=root)
            if src != dst:
                yield src, dst

def determine_extra(pairs, includes=()):
    parents = set((src.parent, dst.parent) for src, dst in pairs)
    for inc in includes:
        for src, dst in parents:
            if (src / inc).exists():
                yield (src / inc, dst / inc)

def rename_file(src, dst):
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

def cbrename(files, *, root, validate=False, dryrun=False):
    paths = expand_paths(files)
    pairs = set(determine_pairs(paths, root=root))

    for src, _ in pairs:
        if not src.exists():
            raise FileNotFoundError('file "{src}" does not exist!')
        # TODO detect or prevent collisions and overwrites

    extra = set(determine_extra(pairs, includes=config['move_includes']))
    union = pairs.union(extra)

    for src, dst in sorted(union, key=itemgetter(1)):
        if dryrun:
            print(f'(dryrun) "{src}" -> "{dst}"')
        else:
            rename_file(src, dst)

    try:
        next(src.parent.iterdir())
    except StopIteration:
        src.parent.rmdir()
