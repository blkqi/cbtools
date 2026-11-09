# This extension sets the 'LocalizedSeries' to the 'english' title if different than the 'romaji' title

def extension(cinfo, data):
    romanji_title = data.get('title', {}).get('romaji') or ''
    english_title = data.get('title', {}).get('english') or ''

    if english_title and english_title != romanji_title:
        cinfo['LocalizedSeries'] = english_title
