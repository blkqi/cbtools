from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.constants import COMICINFO_XML_NAME
from cbtools.exceptions import InvalidArgumentError, FileError
from cbtools.tag.anilist import AniList


def _get_series_id(path):
    if path.is_file():
        path = path.parent

    try:
        with open(path / config['tag.series_id_filename']) as file:
            return int(file.read().strip())
    except FileError:
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
        raise InvalidArgumentError(f'No series ID specified and no {config["tag.series_id_filename"]} found in path!')

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
