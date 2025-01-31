from typing import Dict, Any

# This extension adds the tag 'Highly Rated' to the comic if the average score is 85 or higher.

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    if (data.get('averageScore') or 0) >= 85:
        cinfo['Tags'] = ','.join([cinfo['Tags'], 'Highly Rated'])
