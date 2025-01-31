import os
import shutil
import pathlib
import logging

from string import Template
from cbtools.config import config
from cbtools.core import CBZFile, expand_paths

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

ARG_PATTERN_DEFAULT = '${Series} (${Year})/${Series} ${Volume}'
FILENAME_SUFFIX = '.cbz'
ALLOWED_CHARS = string.ascii_letters + string.digits + " _-~.'!@#$%^&()[]{}"

def cbrename_format(cinfo):
    formatters = {'Series': str,
                  'Volume': lambda x: f'V{int(x.split('.')[0]):02}{f'.{x.split('.')[-1]}' if '.' in x else ''}',
                  'Writer': str,
                  'Year':   str}

    # prefer localized series to series
    cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')

    mapval = lambda k, f: cbrename_sanitize(f(cinfo.get(k)))
    return {k: mapval(k, f) for k, f in formatters.items() if k in cinfo}

def cbrename_substitute(cfile, pattern, default=''):
    template = Template(pattern)
    defaults = {key: default for key in template.get_identifiers()}

    s = template.substitute(defaults, **cbrename_format(cfile.info))
    return pathlib.Path(s.strip())

def cbrename_sanitize(value):
    return ''.join(c for c in value if c in ALLOWED_CHARS)

def cbrename_path(cfile, pattern, *, root):
    stem = cbrename_substitute(cfile, pattern)
    suffix = cfile.Path().suffix
    return (root / stem).with_suffix(suffix)

def cbrename_rename(src, dst):
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

def cbrename_pairs(paths, pattern, **kwds):
    for src in paths:
        with CBZFile(src) as cfile:
            dst = cbrename_path(cfile, pattern, **kwds)
            if src != dst:
                yield src, dst

def cbrename_extra(pairs, includes=()):
    parents = set((src.parent, dst.parent) for src, dst in pairs)
    for inc in includes:
        for src, dst in parents:
            if (src / inc).exists():
                yield (src / inc, dst / inc)

def cbrename(files, pattern=ARG_PATTERN_DEFAULT, validate=False, dryrun=False, **kwds):
    paths = expand_paths(files)
    pairs = set(cbrename_pairs(paths, pattern, **kwds))

    for src, _ in pairs:
        if not src.exists():
            raise FileNotFoundError('file "{src}" does not exist!')
        # TODO detect or prevent collisions and overwrites

    extra = set(cbrename_extra(pairs, includes=config['move_includes']))
    union = pairs.union(extra)

    for src, dst in sorted(union):
        if dryrun:
            print(f'(dryrun) "{src}" -> "{dst}"')
        else:
            cbrename_rename(src, dst)

    try:
        next(src.parent.iterdir())
    except StopIteration:
        src.parent.rmdir()
