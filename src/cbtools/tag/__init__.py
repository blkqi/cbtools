import importlib.resources
import jmespath
import requests
import time

import cbtools.tag.extensions

from cbtools.core import ComicInfo

class AniList:
    def __init__(self):
        self.api_url = 'https://graphql.anilist.co'

    def search(self, series_id):
        media_format = 'MANGA'
        query = importlib.resources.files(__name__).joinpath('anilistid.gql').open().read()
        variables = {
            'series_id': series_id,
            'format': media_format
        }
        response = requests.post(self.api_url, json={'query': query, 'variables': variables})

        if response.status_code == 200:
            return AniListResponse(response.json())
        elif response.status_code == 429:
            wait = int(response.headers['Retry-After'])
            time.sleep(wait+1)
            # TODO: this could loop forever
            return self.search(series_id)
        else:
            # TODO: handle error response?
            return AniListResponse({})

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
        for extension in extensions.__all__:
            module = importlib.import_module(f'cbtools.tag.extensions.{extension}')
            module.extension(cinfo, self.media)
