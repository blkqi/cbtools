from typing import Dict, Any

# This extension sets the 'CommunityRating' field to the AniList percentage score converted to a 0-5 scale.

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    if data.get('averageScore'):
        cinfo['CommunityRating'] = str(round(data['averageScore'] / 20, 1))
