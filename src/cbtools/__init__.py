import zipfile
import lxml.etree

class ComicInfo(dict):
    XSD_FILENAME = 'ComicInfo.xsd'
    XML_FILENAME = 'ComicInfo.xml'

    def __init__(self, *args, **kwds):
        super(ComicInfo, self).__init__(*args, **kwds)

    def parse(f, validate=False):
        xml_tree = lxml.etree.parse(f)

        # TODO allow injecting default values into validated documents
        if validate:
            ComicInfo.validate(xml_tree)

        return ComicInfo((child.tag, child.text) for child in xml_tree.getroot())

    def validate(xml_tree):
        xsd_tree = lxml.etree.parse(ComicInfo.XSD_FILENAME)
        schema = lxml.etree.XMLSchema(xsd_tree)
        schema.assertValid(xml_tree)

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
