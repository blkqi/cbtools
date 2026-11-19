import time
import requests
import jmespath
import importlib.resources
import os

from functools import lru_cache

from cbtools.exceptions import AnilistApiError
from cbtools.log import logger
from cbtools.core import ComicInfo
from cbtools.config import config


class AniListAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, *args, reporter=None, **kwds):
        self.reporter = reporter
        super().__init__(*args, **kwds)

    def send(self, request, **kwds):
        while True:
            response = super().send(request, **kwds)

            if not self._throttle(response):
                break

        return response

    def _throttle(self, response):
        if response.status_code == 429:
            try:
                period = max(1, min(3600, int(response.headers.get('Retry-After', 60))))
            except ValueError:
                logger.warning("Invalid AniList Retry-After header; retrying in 60 seconds.")
                period = 60
            response.close()
            self._wait(period)
            return True
        else:
            return False

    def _wait(self, period):
        if self.reporter is not None:
            self.reporter.waiting(period)
        logger.warning(f'AniList rate limit exceeded! Retry in {period} seconds.')
        time.sleep(period)


class AniList:
    def __init__(self, *, settings=None, reporter=None):
        self.settings = config if settings is None else settings
        self.reporter = reporter
        self.api_url = 'https://graphql.anilist.co'

        adapter = AniListAdapter(reporter=reporter)
        self.session = requests.session()
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        token = os.getenv('ANILIST_TOKEN')
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'

    @lru_cache(1)
    def search(self, series_id):
        query = importlib.resources.files('cbtools.tag').joinpath('anilistid.gql').read_text(encoding='utf-8')
        variables = {
            'series_id': series_id,
        }

        response = self.session.post(self.api_url, json={'query': query, 'variables': variables}, timeout=(10, 60))

        # Token expired/invalid: drop it and retry anonymously.
        if response.status_code == 401 and 'Authorization' in self.session.headers:
            logger.warning('AniList token expired or invalid, falling back to anonymous access.')
            del self.session.headers['Authorization']
            response = self.session.post(self.api_url, json={'query': query, 'variables': variables}, timeout=(10, 60))

        try:
            response.raise_for_status()
        except requests.HTTPError:
            try:
                message = response.json()['errors'][0]['message']
            except (ValueError, KeyError, IndexError, TypeError):
                message = f'AniList entry not found for ID {series_id}'
            raise AnilistApiError(message)

        data = response.json()
        if not isinstance(data, dict) or not (data.get('data') or {}).get('Media'):
            raise AnilistApiError(f'AniList returned no series for ID {series_id}.')
        return AniListResponse(data, settings=self.settings, reporter=self.reporter)


class AniListResponse(dict):
    def __init__(self, *args, settings=None, reporter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = config if settings is None else settings
        self.reporter = reporter

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
        for extension in self.settings['tag.extensions']:
            try:
                module = importlib.import_module(f'cbtools.tag.extensions.{extension}')
                module.extension(cinfo, self.media)
            except ImportError as error:
                if self.reporter is not None:
                    raise AnilistApiError(f'Failed to import tagging extension {extension}: {error}') from error
                logger.error(f'Failed to import extension {extension}')
