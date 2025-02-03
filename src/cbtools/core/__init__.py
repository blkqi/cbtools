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
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

class ComicInfo(dict):
    XSD_FILENAME: Path = importlib.resources.files(__name__).joinpath('ComicInfo.xsd')
    XML_FILENAME: str = 'ComicInfo.xml'

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

    @staticmethod
    def validate(tree: lxml.etree._ElementTree) -> None:
        xsd_tree = lxml.etree.parse(ComicInfo.XSD_FILENAME)
        lxml.etree.XMLSchema(xsd_tree).assertValid(tree)

    def compare(self, with_data: Dict[str, Any], excluding: List[str] = []) -> List[Tuple[str, str, List[Tuple[str, Any]]]]:
        result = dictdiffer.diff(
            {k: v for k, v in self.items() if k not in excluding},
            {k: v for k, v in with_data.items() if k not in excluding})
        return list(result)

class ComicArchiveMember(object):
    def __init__(self, mtime, attr, size, compressed, name):
        self.name = name
        self.attr = attr

    def is_dir(self):
        return self.attr.startswith('D')

class ComicArchive(object):
    _member_struct = struct.Struct('20s 6s 13s 13s')
    _member_name_offset = 52
    _supported_file_types = {
        'cbz': 'zip',
        'cbr': 'rar',
        'cb7': '7z',
    }

    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.volume = None

        self._ftype = self._file_type()
        self._args = ['-y', f'-t{self._ftype}']

    def _file_type(self):
        ext = self.filepath.suffix.strip('.')
        try:
            return next(y for x, y in self._supported_file_types.items() if ext in (x, y))
        except StopIteration:
            raise RuntimeError(f'unsupported file type "{ext}"')

    def info(self) -> ComicInfo:
        buffer = self.read(ComicInfo.XML_FILENAME)
        if buffer:
            return ComicInfo.parse(BytesIO(buffer))
        else:
            return ComicInfo()

    def list(self):
        process = subprocess.run(['7z', 'l', self.filepath, '-ba'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

        for line in BytesIO(process.stdout):
            yield self._parse_member(line)

    def extract(self, targetdir: Path = Path(''), members: List[str] = []) -> None:
        process = subprocess.run(['7z', 'x', self.filepath, *members, *self._args, f'-o{targetdir}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

    def add(self, paths: List[Path]) -> None:
        process = subprocess.run(['7z', 'a', self.filepath, *paths, *self._args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

    def read(self, member: str) -> BytesIO:
        process = subprocess.run(['7z', 'x', self.filepath, member, *self._args, '-so'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            sys.stderr.buffer.write(process.stdout)
            raise RuntimeError(f'7z error code {process.returncode}')

        return process.stdout

    def write(self, member: str, data: bytes) -> None:
        process = subprocess.run(['7z', 'a', self.filepath, *self._args, f'-si{member}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            input=data
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}\n')

    def _parse_member(self, line):
        info, name = (line[:self._member_name_offset], line[self._member_name_offset:])
        data = (x.decode().strip() for x in (*self._member_struct.unpack_from(info), name))
        return ComicArchiveMember(*data)

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
