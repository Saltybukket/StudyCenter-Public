#!/bin/sh
set -eu
program_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$program_dir/desktop-install" "$@"
