import os
import shutil
import string
import logging

from pathlib import Path
from typing import List, Tuple, Generator, Dict, Any
from cbtools.config import config
from cbtools.core import CBZFile, expand_paths
from operator import itemgetter

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

def _path_from_cinfo(cinfo: Dict[str, Any], pattern: str, default: str = _default_missing) -> Path:
    # prefer localized series to series
    cinfo['Series'] = cinfo.get('LocalizedSeries') or cinfo.get('Series')

    template = string.Template(pattern)
    defaults = {key: default for key in template.get_identifiers()}
    values = {k: _sanitize_paths(f(cinfo[k])) for k, f in _formatters() if k in cinfo}
    strpath = template.substitute(defaults, **values)

    return Path(strpath.strip() + '.cbz')

_pattern_missing = config['rename.pattern']

def _construct_rename_pairs(paths: List[Path], *, root: Path, pattern: str = _pattern_missing) -> Generator[Tuple[Path, Path], None, None]:
    for src in paths:
        with CBZFile(src) as cfile:
            if cfile.info:
                dst = root / _path_from_cinfo(cfile.info, pattern=pattern)
                if src != dst:
                    yield src, dst
            else:
                logger.warning(f'file "{src}" contains no info xml - skipping rename')

_includes_missing = config['rename.move_includes']

def _construct_rename_extra(parents: List[Tuple[Path, Path]], includes: List[str] = _includes_missing) -> Generator[Tuple[Path, Path], None, None]:
    for inc in includes:
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
    errors_notfound = set()
    errors_exists = set()
    errors_collision = set()
    checklist_collision = set()

    for src, dst in pairs:
        if not src.exists():
            errors_notfound.add(src)

        if dst.exists():
           errors_exists.add(dst)

        if dst in checklist_collision:
            errors_collision.add(dst)

        checklist_collision.add(dst)

    if errors_exists or errors_collision or errors_notfound:
        for path in errors_notfound:
            logger.error(f'Source {path} doesn\'t exist!')

        for path in errors_exists:
            logger.error(f'Destination {path} already exists!')

        for path in errors_collision:
            logger.error(f'More than one file would be renamed to {path}!')

        return True

    return False

_root_missing = Path('')
_validate_missing = False
_dryrun_missing = False

def cbrename(
    files: List[Path],
    root: Path = _root_missing,
    validate: bool = _validate_missing,
    dryrun: bool = _dryrun_missing
) -> None:

    paths = expand_paths(files)
    pairs = set(_construct_rename_pairs(paths, root=root))

    if _check_has_errors(pairs) and not dryrun:
        return

    parents = set((src.parent, dst.parent) for src, dst in pairs if src.parent != dst.parent)
    extra = set(_construct_rename_extra(parents))
    union = pairs.union(extra)

    for src, dst in sorted(union, key=itemgetter(1)):
        if dryrun:
            print(f'dryrun) "{src}" -> "{dst}"')
        else:
            _rename_file(src, dst)

    for src, _ in parents:
        try:
            next(src.iterdir())
        except StopIteration:
            src.rmdir()
