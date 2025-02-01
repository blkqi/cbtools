import os
import shutil
import string
import logging

from pathlib import Path
from operator import itemgetter
from collections import Counter
from typing import List, Tuple, Generator, Dict, Any
from cbtools.config import config
from cbtools.core import CBZFile, expand_paths

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def _allowed_chars() -> str:
    return string.ascii_letters + string.digits + " _-~.'!@#$%^&()[]{}"

def _sanitize_paths(value: str) -> str:
    return ''.join(c for c in value if c in _allowed_chars())

def _formatters() -> Tuple[Tuple[str, callable], ...]:
    def volume_formatter(volume: str) -> str:
        i, f = str(volume).split('.') if '.' in volume else (str(volume), None)
        return f'V{int(i):02}' + str(f'.{f}' if f else '')

    return (
        ('Series', str),
        ('Writer', str),
        ('Year', str),
        ('Volume', volume_formatter),
    )

_default_missing = ''

def _path_from_cinfo(cinfo: Dict[str, Any], default: str = _default_missing) -> Path:
    # prefer localized series to series
    cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')

    template = string.Template(config['rename.pattern'])
    defaults = {key: default for key in template.get_identifiers()}
    values = {k: _sanitize_paths(f(cinfo[k])) for k, f in _formatters() if k in cinfo}
    strpath = template.substitute(defaults, **values)

    return Path(strpath.strip() + '.cbz')

def _construct_rename_pairs(paths: List[Path], *, root: Path) -> Generator[Tuple[Path, Path], None, None]:
    for src in paths:
        with CBZFile(src) as cfile:
            if cfile.info:
                dst = root / _path_from_cinfo(cfile.info)
                if src != dst:
                    yield src, dst
            else:
                logger.warning(f'file "{src}" contains no info xml - skipping rename')

def _construct_rename_extra(parents: List[Tuple[Path, Path]]) -> Generator[Tuple[Path, Path], None, None]:
    for inc in config['rename.move_includes']:
        for src, dst in parents:
            if (src / inc).exists():
                yield (src / inc, dst / inc)

def _rename_file(src: Path, dst: Path) -> None:
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

def _check_has_errors(pairs: Tuple[Path, Path]) -> bool:
    errors = False

    for path in (src for src, _ in pairs if not src.exists()):
        logger.error(f'Source {path} doesn\'t exist!')
        errors |= True

    for path in (dst for _, dst in pairs if dst.exists()):
        logger.error(f'Destination {path} already exists!')
        errors |= True

    for path in (key for key, val in Counter(dst for _, dst in pairs).items() if val > 1):
        logger.error(f'More than one file would be renamed to {path}!')
        errors |= True

    return errors

def rename(files: List[Path], root: Path = Path(''), dryrun: bool = False) -> None:
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
