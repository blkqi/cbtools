import jmespath
import importlib

from cbtools.exceptions import AnilistApiError
from cbtools.log import logger
from cbtools.core import ComicInfo
from cbtools.config import config
from cbtools.anilist.client import AniListResponse


class AniListMetadata(AniListResponse):
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
