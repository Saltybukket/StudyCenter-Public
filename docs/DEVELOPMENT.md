# Development

Clone this public repository, enter its folder, and run one command:

```powershell
.\start.cmd
```

On Ubuntu 24.04 x64:

```sh
sh start.sh
```

The first run needs internet and disk space for Python, dependencies, Chromium
and portable OCR. It downloads the pinned official Astral uv archive, verifies
its SHA256, and uses uv's checksummed Python distribution. Python 3.12.11
(Astral build 20250918), tool caches and downloads stay under this clone's
ignored `.local/`; application packages go into its own `.venv`. No Python
installation, environment activation, global pip, PATH changes, administrator
rights or Tesseract installer are needed. Windows uses its existing curl.exe and
tar.exe, plus ordinary PowerShell directory/checksum commands; script execution
policies are left unchanged. On Windows the starter supports x64. The
Linux source starter requires existing curl, tar, sha256sum and Ubuntu's
apt-get, apt-cache and dpkg-deb. APT only downloads/extracts portable components
using the existing isolated builder; it does not install host packages.

Setup uses `requirements-windows.lock` or `requirements-linux.lock` as version
constraints. Runtime packages and pytest are installed automatically; optional
speech/model packages are not downloaded. Browser and OCR preparation reuses
the portable build scripts. The starter then opens the application. On Linux,
the local interface opens in the normal desktop browser independently of the
Playwright browser used for explicit source synchronisation.

Run the same command again to start. The complete environment and components
are reused. If a copied or incompatible `.venv` cannot run here, the starter
keeps it under `.local/previous-venv-*` before creating a new environment.
Dependency setup runs again when this clone's path, setup script, platform
lock or project metadata changes, so editable imports follow the current clone.
Linked setup directories are refused. A failed download or setup stops with an
error; retry the same starter after fixing the connection. Incomplete component
directories are preserved and the error identifies which one to move aside.

Useful commands (use `sh start.sh` instead on Linux):

```powershell
.\start.cmd --setup-only
.\start.cmd --no-browser --port 0
.\start.cmd --stop
.\.venv\Scripts\python.exe -m pytest
```

Linux tests use `.venv/bin/python -m pytest`. No application key is needed for
synthetic protocol tests. Tests use isolated directories and never access real
accounts or existing profiles. Default developer data, profiles and credentials
belong to the dedicated `.local/data` Public instance. `--data-dir <new-folder>`
selects a different explicit Public instance; use that same argument when
stopping it. Never point it at another application's data. `--port 0` chooses a
free loopback port; the endpoint appears in the console and instance descriptor.

For ordinary Linux use, download the complete Linux release, extract it, and
run `sh start.sh` in the extracted folder. That package already contains Python,
dependencies and portable components; it does not run developer setup. Its
starter opens the normal desktop browser; `sh start.sh --stop` stops its own
instance. Optional desktop integration remains separate. Distribution sandbox
restrictions on institutional synchronisation do not prevent the local UI from
starting; no sandbox or AppArmor settings are changed.

The bootstrap tool is pinned to [uv 0.8.22](https://github.com/astral-sh/uv/releases/tag/0.8.22).
Its [Python distribution](https://docs.astral.sh/uv/guides/install-python/) comes
from Astral's python-build-standalone project. Tool acquisition runs no remote
installer script. Source edits are reflected by the editable package.

Module contracts are in [CONTRACTS.md](CONTRACTS.md), provider/OAuth details in
[PROVIDERS.md](PROVIDERS.md), and optional speech/MCP in [OPTIONAL.md](OPTIONAL.md).
Keep the existing source evidence, transaction, preview, receipts, undo, backup,
local HTTP/CSRF and credential boundaries. Before commits inspect staged named
files and run the public-content gate plus offline secret scan; before pushes
scan outgoing commit versions. Release artifacts need their existing content,
checksum, source and license checks. See [PLAN.md](PLAN.md) for actual platform
and account acceptance: synthetic tests do not establish live-account or OS
acceptance. Windows install/update/uninstall checks belong in a clean VM.
