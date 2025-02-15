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
        dst_cfile = ComicArchive(dst_path)

        for member in src_cfile.list():
            if member.is_dir():
                continue
            dst_cfile.write(member.name, src_cfile.read(member.name))

        if remove_source:
            src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
