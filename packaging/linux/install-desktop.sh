#!/bin/sh
set -eu
program_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop_dir=${XDG_DATA_HOME:-"$HOME/.local/share"}/applications
mkdir -p "$desktop_dir"
# Desktop files require escaping backslashes and quotes, including paths with spaces.
escaped=$(printf '%s' "$program_dir/start.sh" | sed 's/\\/\\\\/g;s/"/\\"/g;s/%/%%/g')
printf '[Desktop Entry]\nType=Application\nName=StudyCenter-Public\nComment=Local study organisation\nExec="%s"\nTerminal=false\nCategories=Education;\n' "$escaped" > "$desktop_dir/studycenter-public.desktop"
chmod 600 "$desktop_dir/studycenter-public.desktop"
printf 'Desktop entry installed for this user.\n'
