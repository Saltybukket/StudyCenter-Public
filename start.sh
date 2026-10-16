#!/bin/sh
# Project-local tool, Python, environment and components; no host installation.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    echo 'The developer starter supports Linux x64 (Ubuntu 24.04). Use a matching release on other systems.' >&2
    exit 1
fi
local_dir="$root/.local"
tools="$local_dir/tools"
uv_dir="$tools/uv-0.8.22"
for directory in "$local_dir" "$tools" "$uv_dir" "$local_dir/python" "$local_dir/uv-cache" "$local_dir/bin"; do
    if [ -L "$directory" ] || { [ -e "$directory" ] && [ ! -d "$directory" ]; }; then
        echo "Refusing linked setup directory: $directory" >&2
        exit 1
    fi
    mkdir -p "$directory"
done
uv="$uv_dir/uv"
if [ ! -x "$uv" ]; then
    for tool in curl sha256sum tar; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "Missing existing tool: $tool. Use the complete Linux release archive; no host installer was run." >&2
            exit 1
        fi
    done
    echo 'Preparing the project-local Python tool (first start needs internet)...'
    archive="$tools/uv-0.8.22-linux.tar.gz"
    curl --fail --location --proto '=https' --tlsv1.2 'https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-unknown-linux-gnu.tar.gz' --output "$archive"
    printf '%s  %s\n' '741ff1f5742c5a4a25d2f829e8395355e43f7a5ae2ebc6368e9ae2df0efb69cf' "$archive" | sha256sum --check --status || {
        echo 'Official uv archive checksum mismatch. No downloaded program was run.' >&2
        exit 1
    }
    tar -xzf "$archive" --strip-components=1 -C "$uv_dir" uv-x86_64-unknown-linux-gnu/uv
fi
export UV_CACHE_DIR="$local_dir/uv-cache" UV_PYTHON_INSTALL_DIR="$local_dir/python"
export UV_PYTHON_BIN_DIR="$local_dir/bin" UV_PYTHON_INSTALL_BIN=0 UV_PYTHON_CPYTHON_BUILD=20250918
unset VIRTUAL_ENV PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
cd "$root"
"$uv" --no-config python install 3.12.11
python=$("$uv" --no-config python find --system --managed-python 3.12.11)
exec "$python" -I "$root/scripts/setup_dev.py" --uv "$uv" "$@"
