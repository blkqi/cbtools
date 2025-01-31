from typing import Dict, Any

# This extension sets the 'Manga' field to 'Yes'

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    cinfo['Manga'] = 'Yes'
