from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger


_repack_file_type = '.cbz'

def repack(files, **kwds):
    for src_path in expand_paths(files):
        dst_path = src_path.with_suffix(_repack_file_type)

        if dst_path.exists():
            logger.error(f'{dst_path}: already exists!')
            return 1

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        src_cfile = ComicArchive(src_path)
        dst_cfile = ComicArchive(dst_path)

        for member in src_cfile.list():
            dst_cfile.write(member.name, src_cfile.read(member.name))

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
