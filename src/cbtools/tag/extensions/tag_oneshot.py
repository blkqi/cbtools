from typing import Dict, Any

# This extension adds the tag 'Oneshot' to the comic if the comic has only one volume and is finished.

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    if data['volumes'] == 1 and data['status'] == 'FINISHED':
        cinfo['Tags'] = ','.join([cinfo['Tags'], 'Oneshot'])
