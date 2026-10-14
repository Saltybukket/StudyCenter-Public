#!/bin/sh
set -eu
program_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" = "--stop" ]; then
    shift
    exec "$program_dir/StudyCenter-Public" --quit "$@"
fi
exec "$program_dir/StudyCenter-Public" "$@"
