from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.constants import COMICINFO_XML_NAME
from cbtools.exceptions import InvalidArgumentError
from cbtools.tag.anilist import AniList
from cbtools.activity import StageResult


def _get_series_id(path, settings=None):
    settings = config if settings is None else settings
    if path.is_file():
        path = path.parent

    try:
        with open(path / settings['tag.series_id_filename']) as file:
            series_id = int(file.read().strip())
            return series_id if series_id > 0 else None
    except (FileNotFoundError, ValueError):
        return None


def _write_series_id(path, series_id, settings=None):
    settings = config if settings is None else settings
    if not settings['tag.write_series_id_file']:
        return

    series_id_file_path = path / settings['tag.series_id_filename']

    if series_id_file_path.exists():
        return

    try:
        with open(series_id_file_path, 'x') as file:
            file.write(str(series_id))
    except FileExistsError:
        logger.debug("Series ID file was already created: %s", series_id_file_path)


def _fetch_comic_info(client, path, series_id=None, dryrun=False, settings=None):
    settings = config if settings is None else settings

    series_id = series_id or _get_series_id(path, settings)

    if not series_id:
        raise InvalidArgumentError(f'No valid series ID specified or found in {settings["tag.series_id_filename"]}.')

    if not dryrun:
        _write_series_id(path.parent, series_id, settings)

    return client.search(series_id).to_cinfo()


def _tag_comic(path, cinfo, dryrun=False, quiet=False):
    cfile = ComicArchive(path)

    if cfile.volume:
        cinfo['Volume'] = cfile.volume

    diff = (cfile.info()).compare(cinfo, excluding=['Notes'])

    if not diff:
        (logger.debug if quiet else logger.info)(f'{path}: no changes required')
        return False

    if dryrun:
        if not quiet:
            for item in diff:
                print(item)
    else:
        cfile.write(COMICINFO_XML_NAME, cinfo.encode())
    return True


def tag(files, series_id=None, dryrun=False, *, settings=None, reporter=None):
    settings = config if settings is None else settings
    client = AniList(settings=settings, reporter=reporter)
    result = StageResult()
    for path in expand_paths(files):
        resolved_id = series_id or _get_series_id(path, settings)
        if type(resolved_id) is not int or resolved_id <= 0:
            raise InvalidArgumentError(f'No valid series ID specified or found in {settings["tag.series_id_filename"]}.')
        cinfo = _fetch_comic_info(client, path, resolved_id, dryrun, settings)
        if reporter is not None:
            reporter.identity(resolved_id, cinfo.get("Series"))
        if _tag_comic(path, cinfo, dryrun, quiet=reporter is not None):
            result.changed += 1
        else:
            result.unchanged += 1
    return result if reporter is not None else None
