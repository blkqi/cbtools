import tempfile

from pathlib import Path

from cbtools.image import convert_to_webp
from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger


_repack_file_type = '.cbz'


def repack(files, remove_source=False, dryrun=False, root=None, use_webp=False, **kwds):
    for src_path in expand_paths(files):
        if src_path.suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
            logger.debug(f'Skipping {src_path} which is not a supported archive format')
            continue

        dst_path = src_path.with_suffix(_repack_file_type)

        if root is not None:
            parent = src_path.parent.name
            dst_path = Path(root) / parent / dst_path.name

        if src_path == dst_path and not use_webp:
            continue

        if dryrun:
            logger.info(f'dryrun: {src_path} -> {dst_path}')
            continue

        if dst_path.exists() and src_path != dst_path:
            logger.error(f'{dst_path}: already exists!')
            continue

        src_cfile = ComicArchive(src_path)

        has_jpg = False
        if use_webp:
            for member in src_cfile.list():
                name = member.name.lower()
                if name.endswith('.jpg') or name.endswith('.jpeg'):
                    has_jpg = True
                    break

        if use_webp and not has_jpg:
            logger.info(f'Skipping {src_path}: no jpg/jpeg images found for webp conversion')
            continue

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        if use_webp and src_path == dst_path:
            new_src_path = src_path.with_name(src_path.stem + '_source' + src_path.suffix)
            src_path.rename(new_src_path)
            src_path = new_src_path
            src_cfile = ComicArchive(src_path)

        dst_cfile = ComicArchive(dst_path, volume=src_cfile.volume)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            src_cfile.extract_all(out_path=tmp_path)

            if use_webp and has_jpg:
                convert_to_webp(tmp_path)

            dst_cfile.create(str(tmp_path / '*'))

        if remove_source:
            src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
