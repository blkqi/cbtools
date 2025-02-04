import sys
import dictdiffer
import lxml.etree
import platform
import re
import shutil
import tempfile
import subprocess
import zipfile
import struct
import importlib.resources

from io import BytesIO
from pathlib import Path
from operator import itemgetter
from typing import Any, Dict, Generator, List, Optional, Tuple, Union, BinaryIO


class ComicInfo(dict):
    _xml_filename: str = 'ComicInfo.xml'
    _xsd_filename: str = 'ComicInfo.xsd'

    def __init__(self, *args: Any, **kwds: Any) -> None:
        super(ComicInfo, self).__init__(*args, **kwds)

    @staticmethod
    def parse(f: Union[str, bytes]) -> 'ComicInfo':
        tree = lxml.etree.parse(f)
        return ComicInfo((child.tag, child.text) for child in tree.getroot())

    def encode(self, pretty_print: bool = True, **kwds: Any) -> bytes:
        root = lxml.etree.Element('ComicInfo')
        for name, value in self.items():
            lxml.etree.SubElement(root, name).text = str(value or '')
        return lxml.etree.tostring(root, pretty_print=pretty_print, **kwds)

    def validate(self, tree: lxml.etree._ElementTree) -> None:
        xsd_tree = lxml.etree.parse(self._xsd_path())
        lxml.etree.XMLSchema(xsd_tree).assertValid(tree)

    def compare(self, with_data: Dict[str, Any], excluding: List[str] = []) -> List[Tuple[str, str, List[Tuple[str, Any]]]]:
        result = dictdiffer.diff(
            {k: v for k, v in self.items() if k not in excluding},
            {k: v for k, v in with_data.items() if k not in excluding}
        )
        return list(result)

    def _xsd_path(self) -> Path:
        return importlib.resources.files(__name__).joinpath(self._xsd_filename)


class ComicArchiveMember(object):
    def __init__(self, name, mtime, attr, size, compressed):
        self.name = name
        self.attr = attr
        self.size = int(size)

    def is_dir(self):
        return self.attr.startswith('D')


class ComicArchive(object):
    _member_struct = struct.Struct('20s 6s 13s 13s')
    _member_name_offset = 52
    _allowed_file_exts = {
        'cbz': 'zip',
        'cbr': 'rar',
        'cb7': '7z',
    }

    def __init__(self, filepath: Path, filetype: str = None) -> None:
        self.filepath = filepath
        self.volume: Optional[str] = str(float(self._parse_volume())).removesuffix('.0')
        self._type = filetype or self._file_type()
        self._args = ['-y', f'-t{self._type}']

    def _file_type(self):
        ext = self.filepath.suffix.lower().strip('.')
        try:
            return next(y for x, y in self._allowed_file_exts.items() if ext in (x, y))
        except StopIteration:
            raise RuntimeError(f'unsupported file type "{ext}"')

    def _parse_volume(self) -> Optional[str]:
        filename_parts = self.filepath.stem.split(' ')
        filename_parts.reverse()

        for part in filename_parts:
            if match := re.search(r'[vV](\d+\.?\d*)', part):
                return match.group(1)

        for part in filename_parts:
            if match := re.search(r'(\d+)', part):
                return match.group(1)

        # TODO: raise error
        return 0

    def info(self) -> ComicInfo:
        data = self.read(ComicInfo._xml_filename)
        if data:
            return ComicInfo.parse(BytesIO(data))
        else:
            return ComicInfo()

    def list(self) -> Generator[ComicArchiveMember, None, None]:
        process = self._list(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buffer = BytesIO(process.stdout)
        yield from map(self._parse_member, iter(buffer))

    def extract(self, arcname: str, f: BinaryIO) -> None:
        self._extract(arcname, stdout=f, stderr=subprocess.STDOUT)

    def add(self, arcname: str, f: BinaryIO) -> None:
        self._add(arcname, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=f)

    def read(self, arcname: str) -> bytes:
        return self._extract(arcname, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout

    def write(self, arcname: str, data: bytes) -> None:
        self._add(arcname, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, input=data)

    def _list(self, **kwds):
        return _subprocess_run(['7z', 'l', self.filepath, '-ba'], **kwds)

    def _extract(self, arcname: str, **kwds):
        return _subprocess_run(['7z', 'x', self.filepath, arcname, *self._args, '-so'], **kwds)

    def _add(self, arcname: str, **kwds):
        return _subprocess_run(['7z', 'a', self.filepath, *self._args, f'-si{arcname}'], **kwds)

    def _parse_member(self, line: bytes) -> ComicArchiveMember:
        info, name = (line[:self._member_name_offset], line[self._member_name_offset:])
        args = (x.decode().strip() for x in (name, *self._member_struct.unpack_from(info)))
        return ComicArchiveMember(*args)


def _subprocess_run(cmd = List[str], **kwds):
    process = subprocess.run(cmd, **kwds)

    if process.returncode != 0:
        raise RuntimeError(f'{cmd!r} returned error code {process.returncode}\n')

    return process


def expand_paths(paths: List[Path]) -> Generator[Path, None, None]:
    for path in paths:
        if '*' in path.name:
            yield from expand_paths(list(path.parent.glob(path.name)))
        elif path.is_symlink():
            continue
        elif path.is_dir():
            yield from expand_paths(list(path.iterdir()))
        elif path.is_file() and path.suffix.lower() == '.cbz':
            yield path
