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
    images = [p for ext in ('jpg', 'jpeg') for p in Path(root).rglob(f'*.{ext}')]

    pool = multiprocessing.Pool(config['convert.jobs'])

    try:
        pool.map(convert_to_webp, images)

    except KeyboardInterrupt:
        logger.info("Interrupted — terminating workers...")
        pool.terminate()
        pool.join()
        sys.exit(1)

    except Exception as e:
        logger.error(f"Worker error: {e}")
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

        src_cfile = ComicArchive(src_path)
        webp_temp_path = False

        if use_webp:
            has_lossy = lambda m: m.name.lower().endswith(('.jpg', '.jpeg'))

            if not src_cfile.match(has_lossy):
                if dst_path.suffix.lower() == _repack_file_type:
                    logger.info(f'Skipping {src_path}: no jpg images found for webp conversion')
                    continue
            elif src_path == dst_path:
                dst_path = src_path.with_name(dst_path.stem + '_webp' + dst_path.suffix)
                webp_temp_path = True

        if src_path != dst_path and dst_path.exists():
            logger.warning(f'{dst_path}: already exists!')
            continue

        if dryrun:
            logger.info(f'dryrun: {src_path} -> {dst_path}')
            continue

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        dst_cfile = ComicArchive(dst_path, volume=src_cfile.volume)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            src_cfile.extract_all(out_path=tmp_path)

            if use_webp:
                _batch_convert_to_webp(tmp_path)

            files = [str(p) for p in tmp_path.iterdir()]
            dst_cfile.create(*files)

        if remove_source:
            if webp_temp_path:
                dst_path.replace(src_path)
            else:
                src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
