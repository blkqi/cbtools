import dictdiffer
import lxml.etree
import pathlib
import platform
import re
import shutil
import tempfile
import zipfile
import importlib.resources

from typing import Any, Dict, Generator, List, Optional, Tuple, Union

class ComicInfo(dict):
    XSD_FILENAME: pathlib.Path = importlib.resources.files(__name__).joinpath('ComicInfo.xsd')
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
        self.volume: Optional[str] = self._parse_volume()

    def Path(self) -> pathlib.Path:
        return pathlib.Path(self.filename)

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
            temppath = pathlib.Path(tempdir) / 'cbz'

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
        filename_parts = pathlib.Path(self.filename).stem.split(' ')
        filename_parts.reverse()

        for part in filename_parts:
            found = re.search(r'^[vV]{1}\d+\.?\d*$', part)

            if found:
                value = float(found.group(0)[1:])
                return str(value).removesuffix(".0")

        return None

def expand_paths(paths: List[pathlib.Path]) -> Generator[pathlib.Path, None, None]:
    for path in paths:
        if '*' in path.name:
            yield from expand_paths(list(path.parent.glob(path.name)))
        elif path.is_symlink():
            continue
        elif path.is_dir():
            yield from expand_paths(list(path.iterdir()))
        elif path.is_file() and path.suffix.lower() == '.cbz':
            yield path
