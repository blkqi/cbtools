import tempfile

from pathlib import Path

from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger


_repack_file_type = '.cbz'

def repack(files, remove_source=False, dryrun=False, **kwds):
    for src_path in expand_paths(files):
        if src_path.suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
            logger.debug(f'Skipping {src_path} which is not a supported archive format')
            continue

        dst_path = src_path.with_suffix(_repack_file_type)

        if src_path == dst_path:
            continue

        if dryrun:
            logger.info(f'dryrun: {src_path} -> {dst_path}')
            continue

        if dst_path.exists():
            logger.error(f'{dst_path}: already exists!')
            return 1

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        src_cfile = ComicArchive(src_path)
        dst_cfile = ComicArchive(dst_path, volume=src_cfile.volume)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            for member in src_cfile.list():
                if member.is_dir():
                    continue

                file_path = tmpdir_path / member.name
                file_path.parent.mkdir(parents=True, exist_ok=True)

                with open(file_path, 'wb') as f:
                    f.write(src_cfile.read(member.name))

            dst_cfile.create(str(tmpdir_path / '*'))

        if remove_source:
            src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
