import importlib.resources
import jmespath
import requests
import time
import logging

from typing import Optional, List, Dict, Any

import cbtools.tag.extensions
from cbtools.config import config
from cbtools.core import ComicInfo, CBZFile, expand_paths

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

class AniListAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, *args: Any, **kwds: Any) -> None:
        super().__init__(*args, **kwds)

    def send(self, request: requests.PreparedRequest, **kwds: Any) -> requests.Response:
        while True:
            response = super().send(request, **kwds)

            if not self._throttle(response):
                break

        return response

    def _throttle(self, response: requests.Response) -> bool:
        if response.status_code == 429:
            self._wait(int(response.headers['Retry-After']))
            return True
        else:
            return False

    def _wait(self, period: int) -> None:
        logger.warn(f'AniList rate limit exceeded! Retry in {period} seconds.')
        time.sleep(period)

class AniList:
    def __init__(self) -> None:
        self.api_url = 'https://graphql.anilist.co'

        adapter = AniListAdapter()
        self.session = requests.session()
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def search(self, series_id: int) -> 'AniListResponse':
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
    ANILIST_COMICINFO_JMESMAP: Dict[str, str] = {
        'Series': 'title.romaji',
        'LocalizedSeries': 'title.english',
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
    def media(self) -> Dict[str, Any]:
        return self.get('data', {}).get('Media', {})

    def to_cinfo(self) -> ComicInfo:
        cinfo = ComicInfo()
        data = self.get('data', {}).get('Media', {})

        if not data:
            return cinfo

        cinfo = self._map_cinfo(self.ANILIST_COMICINFO_JMESMAP, cinfo)

        return cinfo

    def _map_cinfo(self, jmesmap: Dict[str, str], cinfo: Optional[ComicInfo] = None) -> ComicInfo:
        cinfo = cinfo or ComicInfo()

        for target_key, source_key in jmesmap.items():
            value = jmespath.search(source_key, self.media)

            if value:
                cinfo[target_key] = str(value)

        self._apply_extensions(cinfo)

        return cinfo

    def _apply_extensions(self, cinfo: ComicInfo) -> None:
        for extension in extensions.__all__:
            module = importlib.import_module(f'cbtools.tag.extensions.{extension}')
            module.extension(cinfo, self.media)

def _get_series_id(path: 'Path') -> Optional[int]:
    if path.is_file():
        path = path.parent

    try:
        with open(path / config['tag.series_id_filename']) as file:
            return int(file.read().strip())
    except FileNotFoundError:
        return None

def _write_series_id(path: 'Path', series_id: int) -> None:
    if not config['tag.write_series_id_file']:
        return

    series_id_file_path = path / config['tag.series_id_filename']

    if series_id_file_path.exists():
        return

    with open(series_id_file_path, 'w') as file:
        file.write(str(series_id))

def cbtag(files: List[str], series_id: Optional[int] = None, dryrun: bool = False) -> None:
    paths = expand_paths(files)
    cinfo = None

    for path in paths:
        if not series_id:
            series_id = _get_series_id(path)

            if not series_id:
                raise NameError(f"No series ID specified and no {config['tag.series_id_filename']} found in path!")

        if not cinfo:
            if not dryrun:
                _write_series_id(path.parent, series_id)

            cinfo = AniList().search(series_id).to_cinfo()

        with CBZFile(path) as cfile:
            if cfile.volume:
                cinfo['Volume'] = cfile.volume

            diff = cfile.info.compare(cinfo, excluding=['Notes'])

            if not diff:
                logger.info(f'{path}: no changes required')
                continue

            if dryrun:
                for item in diff:
                    print(item)
            else:
                cfile.update_cinfo(cinfo)
