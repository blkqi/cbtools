import dictdiffer
import jmespath
import lxml.etree
import pathlib
import re
import requests
import shutil
import tempfile
import zipfile

class AniList:
    def __init__(self):
        self.api_url = 'https://graphql.anilist.co'

    def search(self, series_id):
        media_format = 'MANGA'
        query = '''
                query search_details_by_series_id ($series_id: Int, $format: MediaFormat) {
                Media (id: $series_id, type: MANGA, format: $format) {
                    id
                    volumes
                    siteUrl
                    title {
                    romaji
                    english
                    }
                    studios {
                    edges {
                        node {
                        name
                        }
                    }
                    }
                    staff {
                    edges {
                        role
                        node {
                        name {
                            full
                        }
                        }
                    }
                    }
                    genres
                    tags {
                    name
                    }
                    description
                    startDate {
                    day
                    month
                    year
                    }
                }
                }
                '''
        variables = {
            'series_id': series_id,
            'format': media_format
        }
        response = requests.post(self.api_url, json={'query': query, 'variables': variables})
        return AniListResponse(response.json())

class AniListResponse(dict):
    ANILIST_COMICINFO_JMESMAP = {
        'Series': 'title.romaji',
        'LocalizedSeries': 'title.english',
        'Count': 'volumes',
        'Writer': 'staff.edges[?role.contains(@, `Story`)].node.name.full | [0]',
        'Penciller': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Inker': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Colorist': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'CoverArtist': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Publisher': 'studios.edges[?isMain==true].node.name | [0]',
        'Genre': 'genres[*] | join(`,`, @)',
        'Tags': 'tags[*].name | join(`,`, @)',
        'Summary': 'description',
        'Web': 'siteUrl',
        'Year': 'startDate.year',
        'Month': 'startDate.month',
        'Day': 'startDate.day',
    }

    def to_cinfo(self):
        cinfo = ComicInfo()
        data = self.get('data', {}).get('Media', {})

        if not data:
            return cinfo

        cinfo.map(data, self.ANILIST_COMICINFO_JMESMAP)

        return cinfo

class ComicInfo(dict):
    XSD_FILENAME = 'ComicInfo.xsd'
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

    def compare(self, with_data):
        result = dictdiffer.diff(self, with_data)
        for item in result:
            print(item)

    def map(self, source, jmesmap):
        for target_key, source_key in jmesmap.items():
            value = jmespath.search(source_key, source)

            if value:
                self[target_key] = str(value)

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
            found = re.search(r'^[vV]{1}\d+$', part)

            if found:
                return str(int(found.group(0)[1:]))
