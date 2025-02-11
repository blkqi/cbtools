import importlib.resources
import jmespath
import requests
import time
import logging

from pathlib import Path

from cbtools.log import logger
from cbtools.core import ComicInfo, ComicArchive, expand_paths
from cbtools.config import config
from cbtools.constants import COMICINFO_XML_NAME

class AniListAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

    def send(self, request, **kwds):
        while True:
            response = super().send(request, **kwds)

            if not self._throttle(response):
                break

        return response

    def _throttle(self, response):
        if response.status_code == 429:
            self._wait(int(response.headers['Retry-After']))
            return True
        else:
            return False

    def _wait(self, period):
        logger.warn(f'AniList rate limit exceeded! Retry in {period} seconds.')
        time.sleep(period)

class AniList:
    def __init__(self):
        self.api_url = 'https://graphql.anilist.co'

        adapter = AniListAdapter()
        self.session = requests.session()
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def search(self, series_id):
        media_format = 'MANGA'
        query = importlib.resources.files(__name__).joinpath('anilistid.gql').open().read()
        variables = {
            'series_id': series_id,
            'format': media_format
        }

        response = self.session.post(self.api_url, json={'query': query, 'variables': variables})
        response.raise_for_status()

        return AniListResponse(response.json())

class AniListResponse(dict):
    ANILIST_COMICINFO_JMESMAP = {
        'Series': 'title.romaji',
        'Count': 'volumes',
        'Writer': 'staff.edges[?role.contains(@, `Story`)].node.name.full | [0]',
        'Penciller': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Inker': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Colorist': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'CoverArtist': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Letterer': 'staff.edges[?role.contains(@, `Art`)].node.name.full | [0]',
        'Publisher': 'studios.edges[?isMain==true].node.name | [0]',
        'Genre': 'genres[*] | join(`,`, @)',
        'Tags': 'tags[*].name | join(`,`, @)',
        'Summary': 'description',
        'Web': 'siteUrl',
        'Year': 'startDate.year',
        'Month': 'startDate.month',
        'Day': 'startDate.day',
    }

    @property
    def media(self):
        return self.get('data', {}).get('Media', {})

    def to_cinfo(self):
        cinfo = ComicInfo()
        data = self.get('data', {}).get('Media', {})

        if not data:
            return cinfo

        cinfo = self._map_cinfo(self.ANILIST_COMICINFO_JMESMAP, cinfo)

        return cinfo

    def _map_cinfo(self, jmesmap, cinfo=None):
        cinfo = cinfo or ComicInfo()

        for target_key, source_key in jmesmap.items():
            value = jmespath.search(source_key, self.media)

            if value:
                cinfo[target_key] = str(value)

        self._apply_extensions(cinfo)

        return cinfo

    def _apply_extensions(self, cinfo):
        for extension in config['tag.extensions']:
            try:
                module = importlib.import_module(f'cbtools.tag.extensions.{extension}')
                module.extension(cinfo, self.media)
            except ImportError:
                logger.error(f'Failed to import extension {extension}')

def _get_series_id(path):
    if path.is_file():
        path = path.parent

    try:
        with open(path / config['tag.series_id_filename']) as file:
            return int(file.read().strip())
    except FileNotFoundError:
        return None

def _write_series_id(path, series_id):
    if not config['tag.write_series_id_file']:
        return

    series_id_file_path = path / config['tag.series_id_filename']

    if series_id_file_path.exists():
        return

    with open(series_id_file_path, 'w') as file:
        file.write(str(series_id))

def _fetch_comic_info(client, path, series_id=None, dryrun=False):

    series_id = series_id or _get_series_id(path)

    if not series_id:
        raise NameError(f'No series ID specified and no {config["tag.series_id_filename"]} found in path!')

    if not dryrun:
        _write_series_id(path.parent, series_id)

    return client.search(series_id).to_cinfo()

def _tag_comic(path, cinfo, dryrun=False):
    cfile = ComicArchive(path)

    if cfile.volume:
        cinfo['Volume'] = cfile.volume

    diff = (cfile.info()).compare(cinfo, excluding=['Notes'])

    if not diff:
        logger.info(f'{path}: no changes required')
        return

    if dryrun:
        for item in diff:
            print(item)
    else:
        cfile.write(COMICINFO_XML_NAME, cinfo.encode())

def tag(files, series_id=None, dryrun=False):
    client = AniList()
    for path in expand_paths(files):
        cinfo = _fetch_comic_info(client, path, series_id, dryrun)
        _tag_comic(path, cinfo, dryrun)
