import multiprocessing
import os
import tempfile

from glob import glob
from pathlib import Path

from cbtools import config
from cbtools.image import convert_to_webp
from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger
from cbtools.activity import StageIssue, StageResult
from cbtools.exceptions import SubprocessError


_repack_file_type = '.cbz'


def _batch_convert_to_webp(root, jobs=None):
    images = [p for ext in ('jpg', 'jpeg') for p in Path(root).rglob(f'*.{ext}')]

    pool = multiprocessing.Pool(config['convert.jobs'] if jobs is None else jobs)

    try:
        pool.map(convert_to_webp, images)

    except KeyboardInterrupt:
        logger.info("Interrupted — terminating workers...")
        pool.terminate()
        pool.join()
        raise

    except Exception as e:
        pool.terminate()
        pool.join()
        raise SubprocessError(f"WebP conversion failed: {e}") from e

    else:
        pool.close()
        pool.join()


def repack(files, remove_source=False, dryrun=False, root=None, use_webp=False,
           settings=None, reporter=None, **kwds):
    settings = config if settings is None else settings
    result = StageResult()
    for src_path in expand_paths(files):
        if src_path.suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
            logger.debug(f'Skipping {src_path} which is not a supported archive format')
            continue

        dst_path = src_path.with_suffix(_repack_file_type)

        if root is not None:
            parent = src_path.parent.name
            dst_path = Path(root) / parent / dst_path.name

        if src_path == dst_path and not use_webp:
            result.unchanged += 1
            continue

        src_cfile = ComicArchive(src_path)
        webp_temp_path = False

        has_lossy = lambda m: m.name.lower().endswith(('.jpg', '.jpeg'))
        convert_webp = use_webp and src_cfile.match(has_lossy)

        if use_webp and not convert_webp:
            if src_path == dst_path:
                (logger.debug if reporter else logger.info)(
                    f'Skipping {src_path}: no jpg images found for webp conversion')
                result.unchanged += 1
                continue

        if convert_webp and src_path == dst_path:
            dst_path = src_path.with_name(dst_path.stem + '_webp' + dst_path.suffix)
            webp_temp_path = True

        if src_path != dst_path and dst_path.exists():
            if reporter is None:
                logger.warning(f'{dst_path}: already exists!')
            result.failed += 1
            result.issues.append(StageIssue(
                "REPACK_CONFLICT", f"Destination already exists: {dst_path}",
                "Resolve the conflicting archive, then rescan the folder.",
            ))
            continue

        if dryrun:
            logger.info(f'dryrun: {src_path} -> {dst_path}')
            result.changed += 1
            result.outputs.append(str(dst_path))
            continue

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        dst_cfile = ComicArchive(dst_path, volume=src_cfile.volume)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            src_cfile.extract_all(out_path=tmp_path)

            if convert_webp:
                _batch_convert_to_webp(tmp_path, settings['convert.jobs'])

            files = [str(p) for p in tmp_path.iterdir()]
            dst_cfile.create(*files)

        if remove_source:
            if webp_temp_path:
                dst_path.replace(src_path)
            else:
                src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
        result.changed += 1
        result.outputs.append(str(src_path if webp_temp_path and remove_source else dst_path))

    return result if reporter is not None else None
