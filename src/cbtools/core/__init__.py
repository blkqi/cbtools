import sys
import dictdiffer
import lxml.etree
import platform
import re
import shutil
import tempfile
import subprocess
import zipfile
import importlib.resources

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

class ComicArchive(object):
    _comic_info_name = 'ComicInfo.xml'

    def __init__(self, filepath: Path):
        self.filepath = filepath

        #TODO handle more type
        assert(self.filepath.suffix == '.cbz')

    def info(self):
        return ComicInfo.parse(self.read([self._comic_info_name]))

    def extract(self, targetdir: Path = Path(''), members: List[str] = []):
        process = subprocess.run(['7z', 'x', '-y', '-o' + targetdir, self.filepath] + members,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

    def read(self, members: List[str] = []):
        process = subprocess.run(['7z', 'x', '-y', '-so', self.filepath] + members,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

        return BytesIO(process.stdout)

    def add(self, paths: List[Path]):
        process = subprocess.run(['7z', 'a', '-y', self.filepath] + paths,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        if process.returncode != 0:
            raise RuntimeError(f'7z error code {process.returncode}')

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

class CBZFile(zipfile.ZipFile):
    def __init__(self, file: Union[str, bytes], **kwds: Any) -> None:
        super(CBZFile, self).__init__(file, **kwds)
        self.info: ComicInfo = self._get_info()
        self.volume: Optional[str] = str(float(self._parse_volume())).removesuffix('.0')

    def Path(self) -> Path:
        return Path(self.filename)

    def extractall(self, path: Optional[Union[str, bytes]] = None, members: Optional[List[zipfile.ZipInfo]] = None, pwd: Optional[bytes] = None, flatten: bool = False) -> None:
        if not flatten:
            return super().extractall(path=path, members=members, pwd=pwd)

        for member in self.infolist():
            if member.is_dir():
                continue

            member.filename = member.filename.replace('/', '__')
            self.extract(member, path)

    def update_cinfo(self, cinfo: ComicInfo) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temppath = Path(tempdir) / 'cbz'

            with CBZFile(temppath, mode='w') as cbzwrite:
                cbzwrite.writestr(ComicInfo.XML_FILENAME, cinfo.encode())

                for item in self.infolist():
                    if item.filename != ComicInfo.XML_FILENAME:
                        data = self.read(item.filename)
                        cbzwrite.writestr(item, data)

            shutil.copyfile(temppath, self.filename)
            temppath.unlink()

    def _get_info(self) -> ComicInfo:
        try:
            with self.open(ComicInfo.XML_FILENAME) as c:
                return ComicInfo.parse(c)
        except KeyError:
            pass

        return ComicInfo()

    def _parse_volume(self) -> Optional[str]:
        filename_parts = Path(self.filename).stem.split(' ')
        filename_parts.reverse()

        for part in filename_parts:
            if match := re.search(r'[vV](\d+\.?\d*)', part):
                return match.group(1)

        for part in filename_parts:
            if match := re.search(r'(\d+)', part):
                return match.group(1)

        return None

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
