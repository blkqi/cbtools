import time
import requests
import jmespath
import importlib.resources

from functools import lru_cache

from cbtools.exceptions import AnilistEntryNotFound
from cbtools.log import logger
from cbtools.core import ComicInfo
from cbtools.config import config


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

    @lru_cache(1)
    def search(self, series_id):
        query = importlib.resources.files(__name__).joinpath('anilistid.gql').open().read()
        variables = {
            'series_id': series_id,
        }

        response = self.session.post(self.api_url, json={'query': query, 'variables': variables})

        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise AnilistEntryNotFound(f'AniList entry not found for ID {series_id}')

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
