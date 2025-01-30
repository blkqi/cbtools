import os
import string
import shutil
import pathlib
import logging

from cbtools.config import config
from cbtools.core import CBZFile, expand_paths

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

ARG_PATTERN_DEFAULT = '${Series} (${Year})/${Series} ${Volume}'

class CBZRenamer(object):
    FILENAME_SUFFIX = '.cbz'
    ALLOWED_CHARS = string.ascii_letters + string.digits + " _-~.'!@#$%^&()[]{}"

    def __init__(self, pattern, default=''):
        self._template = string.Template(pattern)
        self._defaults = {key: default for key in self._template.get_identifiers()}

    def _substitute(self, cfile):
        cinfo = cfile.info
        formatters = {'Series': str,
                      'Volume': lambda x: f'V{int(x.split('.')[0]):02}{f'.{x.split('.')[-1]}' if '.' in x else ''}',
                      'Writer': str,
                      'Year':   str}
        cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')
        inputs = {key: self._santiize(fun(cinfo.get(key)))
              for key, fun in formatters.items() if key in cinfo}
        return self._template.substitute(self._defaults, **inputs).strip()

    def _santiize(self, value):
        return ''.join(c for c in value if c in self.ALLOWED_CHARS)

    def path(self, cfile, path=pathlib.Path('')):
        stem = pathlib.Path(self._substitute(cfile))
        return (path / stem).with_suffix(stem.suffix + self.FILENAME_SUFFIX)

    def rename(src, dst):
        # create required directory structure
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

        # errno 18: target filesystem differs - fallback to copy file
        shutil.copyfile(src, dst)
        src.unlink()

def cbrename(files, pattern=ARG_PATTERN_DEFAULT, validate=False, dryrun=False, **kwds):
    files = expand_paths(files)
    renamer = CBZRenamer(pattern)
    parents = {}

    for path in files:
        with CBZFile(path) as cfile:
            newpath = renamer.path(cfile, **kwds)

        if path == newpath:
            continue
        elif dryrun:
            print(f'(dryrun) "{path}" -> "{newpath}"')
        else:
            CBZRenamer.rename(path, newpath)

        parents[pathlib.Path(path).parent] = pathlib.Path(newpath).parent

    for path, newpath in parents.items():
        for include in config['move_includes']:
            include_path = path / include

            if include_path.exists():
                new_include_path = newpath / include

                if include_path == new_include_path:
                    continue

                if dryrun:
                    print(f'(dryrun) "{include_path}" -> "{new_include_path}"')
                else:
                    include_path.rename(new_include_path)

        if next(path.iterdir(), None) is None:
            if not dryrun:
                path.rmdir()
