import zipfile
import lxml.etree

class ComicInfo(dict):
    XSD_FILENAME = 'ComicInfo.xsd'
    XML_FILENAME = 'ComicInfo.xml'

    def __init__(self, *args, **kwds):
        super(ComicInfo, self).__init__(*args, **kwds)

    def parse(f, validate=False):
        tree = lxml.etree.parse(f)

        # TODO allow injecting default values into validated documents
        if validate:
            ComicInfo.validate(tree)

        return ComicInfo((child.tag, child.text) for child in tree.getroot())

    def encode(self, **kwds):
        root = lxml.etree.Element('ComicInfo')

        for name, value in self.items():
            lxml.etree.SubElement(root, name).text = str(value or '')

        return lxml.etree.tostring(root, **kwds)

    def validate(tree):
        xsd_tree = lxml.etree.parse(ComicInfo.XSD_FILENAME)
        lxml.etree.XMLSchema(xsd_tree).assertValid(tree)

class CBZFile(zipfile.ZipFile):
    def __init__(self, file, **kwds):
        super(CBZFile, self).__init__(file, **kwds)

    def info(self, validate=False):
        try:
            with self.open(ComicInfo.XML_FILENAME) as c:
                return ComicInfo.parse(c, validate=validate)
        except KeyError:
            pass

        return ComicInfo()

    def extractall(self, path=None, members=None, pwd=None, flatten=False):
        if not flatten:
            return super().extractall(path=path, members=members, pwd=pwd)

        for member in self.infolist():
            if member.is_dir():
                continue

            member.filename = member.filename.replace('/', '__')

            self.extract(member, path)
