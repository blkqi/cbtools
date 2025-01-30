import dictdiffer
import lxml.etree
import pathlib
import platform
import re
import shutil
import tempfile
import zipfile
import importlib.resources

from decimal import Decimal

class ComicInfo(dict):
    XSD_FILENAME = importlib.resources.files(__name__).joinpath('ComicInfo.xsd')
    XML_FILENAME = 'ComicInfo.xml'

    def __init__(self, *args, **kwds):
        super(ComicInfo, self).__init__(*args, **kwds)

    def parse(f):
        tree = lxml.etree.parse(f)

        return ComicInfo((child.tag, child.text) for child in tree.getroot())

    def encode(self, pretty_print=True, **kwds):
        root = lxml.etree.Element('ComicInfo')

        for name, value in self.items():
            lxml.etree.SubElement(root, name).text = str(value or '')

        return lxml.etree.tostring(root, pretty_print=pretty_print, **kwds)

    def validate(tree):
        xsd_tree = lxml.etree.parse(ComicInfo.XSD_FILENAME)
        lxml.etree.XMLSchema(xsd_tree).assertValid(tree)

    def compare(self, with_data, excluding=[]):
        result = dictdiffer.diff(
            {k: v for k, v in self.items() if k not in excluding},
            {k: v for k, v in with_data.items() if k not in excluding})
        return list(result)

class CBZFile(zipfile.ZipFile):
    def __init__(self, file, **kwds):
        super(CBZFile, self).__init__(file, **kwds)
        self.info = self._get_info()
        self.volume = self._parse_volume()

    def extractall(self, path=None, members=None, pwd=None, flatten=False):
        if not flatten:
            return super().extractall(path=path, members=members, pwd=pwd)

        for member in self.infolist():
            if member.is_dir():
                continue

            member.filename = member.filename.replace('/', '__')

            self.extract(member, path)

    def update_cinfo(self, cinfo):
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

    def _get_info(self):
        try:
            with self.open(ComicInfo.XML_FILENAME) as c:
                return ComicInfo.parse(c)
        except KeyError:
            pass

        return ComicInfo()

    def _parse_volume(self):
        filename_parts = self.filename.split(' ')
        filename_parts.reverse()

        for part in filename_parts:
            found = re.search(r'^[vV]{1}\d+\.?\d*$', part)

            if found:
                return str(Decimal(found.group(0)[1:]).normalize())

def expand_paths(paths):
    for path in paths:
        if '*' in path.name:
            yield from expand_paths(path.parent.glob(path.name))
        elif path.is_symlink():
            continue
        elif path.is_dir():
            yield from expand_paths(path.iterdir())
        elif path.is_file() and path.suffix.lower() == '.cbz':
            yield path
