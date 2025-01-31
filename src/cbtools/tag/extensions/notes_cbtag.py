from datetime import datetime, timezone
from typing import Dict, Any

# This extension sets the 'Notes' field with tagger information.

def extension(cinfo: Dict[str, Any], data: Dict[str, Any]) -> None:
    cinfo['Notes'] = f'Tagged by cbtools on {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}Z'
