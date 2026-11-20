import time
import requests
import importlib.resources

from functools import lru_cache

from cbtools.exceptions import AnilistApiError
from cbtools.log import logger
from cbtools.anilist.auth import anilist_auth


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
    def __init__(self, *, reporter=None):
        self.reporter = reporter
        self.api_url = 'https://graphql.anilist.co'

        adapter = AniListAdapter(reporter=reporter)
        self.session = requests.session()
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        token = anilist_auth.token()
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'

    @lru_cache(1)
    def search(self, series_id):
        query = importlib.resources.files('cbtools.anilist').joinpath('anilistid.gql').read_text(encoding='utf-8')
        variables = {
            'series_id': series_id,
        }

        response = self.session.post(self.api_url, json={'query': query, 'variables': variables}, timeout=(10, 60))

        # Token expired/invalid: drop it and retry anonymously.
        if response.status_code == 401 and 'Authorization' in self.session.headers:
            anilist_auth.reject(self.session.headers['Authorization'].removeprefix('Bearer '))
            response.close()
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
        return AniListResponse(data)


class AniListResponse(dict):
    @property
    def media(self):
        return self.get('data', {}).get('Media', {})
