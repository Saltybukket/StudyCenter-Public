#!/bin/sh
set -eu
program_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$program_dir/StudyCenter-Public" ]; then
    echo 'StudyCenter-Public executable missing or not executable. Extract the complete Linux archive and run sh start.sh from that folder.' >&2
    exit 1
fi
if [ "${1:-}" = "--stop" ]; then
    shift
    exec "$program_dir/StudyCenter-Public" --quit "$@"
fi
# The application opens the normal desktop browser. Playwright is used only for
# explicit institutional source synchronisation, never to display the local UI.
exec "$program_dir/StudyCenter-Public" --browser "$@"
