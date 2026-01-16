import abc
import tempfile

from pathlib import Path

from cbtools.image import convert_to_webp
from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.core import ComicArchive, expand_paths
from cbtools.log import logger


_repack_file_type = '.cbz'


class RepackPipeline:
    def __init__(self, processors):
        self._processors = processors

    def run(self, src_path):
        with tempfile.TemporaryDirectory() as tmp:
            cfile = ComicArchive(path)
            cfile.extract_all(out_path=tmp)

            for p in self._processors:
                p.intialize()

            for p in self._processors:
                p.process(tmp)

            for p in self._processors:
                p.finalize()


class Processor(abc.ABC):
    def initialize(self): pass
    def process(self, path): pass
    def finalize(self): pass


class ImageTranscoder(Processor):
    def process(self, path):
        _batch_convert_to_webp(path)


class ComicArchiveWriter(Processor):
    def __init__(self, path, **kwds):
        self._cfile = ComicArchive(path, **kwds)

    def process(self, path):
        self._cfile.create(f'{path}/*')


def repack(files, remove_source=False, dryrun=False, root=None, use_webp=False, **kwds):
    for src_path in expand_paths(files):
        if src_path.suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
            logger.debug(f'Skipping {src_path} which is not a supported archive format')
            continue

        dst_path = src_path.with_suffix(_repack_file_type)

        if root is not None:
            parent = src_path.parent.name
            dst_path = Path(root) / parent / dst_path.name

        if dryrun:
            logger.info(f'dryrun: {src_path} -> {dst_path}')
            continue

        if dst_path.exists() and src_path != dst_path:
            logger.error(f'{dst_path}: already exists!')
            continue

        logger.debug(f'repack starting: {src_path} -> {dst_path}')

        processors = [
            ImageTranscoder(),
            ComicArchiveWriter(dst_path)
        ]

        pipeline = RepackPipeline(processors)
        pipeline.run(src_path)

        if remove_source:
            src_path.unlink()

        logger.debug(f'repack complete: {src_path} -> {dst_path}')
