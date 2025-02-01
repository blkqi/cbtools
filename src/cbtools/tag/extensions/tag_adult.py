from typing import Dict, Any

# This extension adds the tag 'Adult' to the comic if the 'isAdult' field is True.

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    if data['isAdult']:
        cinfo['Tags'] = ','.join([cinfo['Tags'], 'Adult'])
