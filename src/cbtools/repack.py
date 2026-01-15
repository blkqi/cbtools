import multiprocessing
import os
import sys
import tempfile

from glob import glob
from pathlib import Path

from cbtools import config
from cbtools.image import convert_to_webp
from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger


_repack_file_type = '.cbz'


def _batch_convert_to_webp(root):
    images = [p for ext in ('*.jpg', '*.jpeg') for p in map(Path, glob(os.path.join(root, ext)))]

    pool = multiprocessing.Pool(config['convert.jobs'])

    try:
        pool.map(convert_to_webp, images)

    except KeyboardInterrupt:
        print("Interrupted — terminating workers...")
        pool.terminate()
        pool.join()
        sys.exit(1)

    except Exception as e:
        print(f"Worker error: {e}")
        pool.terminate()
        pool.join()
        sys.exit(1)

    else:
        pool.close()
        pool.join()


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
            logger.warning(f'{dst_path}: already exists!')
            continue

        src_cfile = ComicArchive(src_path)

        if use_webp and src_path == dst_path:
            has_lossy = lambda m: m.name.lower().endswith(('.jpg', '.jpeg'))

            if src_cfile.match(has_lossy):
                src_cfile.rename(src_path.stem + '_source' + src_path.suffix)
            else:
                logger.info(f'Skipping {src_path}: no jpg images found for webp conversion')
                continue

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        dst_cfile = ComicArchive(dst_path, volume=src_cfile.volume)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            src_cfile.extract_all(out_path=tmp_path)

            if use_webp:
                _batch_convert_to_webp(tmp_path)

            dst_cfile.create(str(tmp_path / '*'))

        if remove_source:
            src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
