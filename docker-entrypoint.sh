#!/bin/sh

# Honor PUID / PGID / UMASK env vars.
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-0002}"

umask "$UMASK"

# If already running as the requested uid/gid (e.g. docker run -u was used),
# just exec directly — no need for setpriv.
if [ "$(id -u)" = "$PUID" ] && [ "$(id -g)" = "$PGID" ]; then
    exec "$@"
fi

# Otherwise we must be root to switch users.
if [ "$(id -u)" != "0" ]; then
    echo "entrypoint: cannot switch to ${PUID}:${PGID} (not root, current uid=$(id -u))" >&2
    exec "$@"
fi

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
