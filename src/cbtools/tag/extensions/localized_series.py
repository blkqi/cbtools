from typing import Dict, Any

# This extension sets the 'LocalizedSeries' to the 'english' title if different than the 'romaji' title

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    romanji_title = data.get('title', {}).get('romaji') or ''
    english_title = data.get('title', {}).get('english') or ''

    if english_title and english_title.lower() != romanji_title.lower():
        cinfo['LocalizedSeries'] = english_title
