# Development

Python 3.12, one FastAPI process, SQLite and a common local browser interface.
Install the pinned dependency set into a new environment for this repository:

```sh
python -m venv .venv
python -m pip install -e '.[dev,speech]'
python -m studycenter_public --data-dir ./isolated-test-data --port 0 --no-browser
python -m pytest
```

Use the environment's Python executable when invoking commands. Runtime data
must be outside the package directory. `--port 0` reserves a free loopback port;
the actual endpoint is recorded only in that instance's runtime descriptor.
`--quit --data-dir <same path>` requests cooperative shutdown.

Module contracts are in [CONTRACTS.md](CONTRACTS.md). Provider/OAuth details are in
[PROVIDERS.md](PROVIDERS.md); optional speech and local MCP in [OPTIONAL.md](OPTIONAL.md).
No application key is needed to run synthetic protocol tests. Tests never access
real source accounts or existing profiles.

Source synchronisation is explicit. Document originals and prior readings are
retained; only a trusted selected-course binding may merge course-level identities.
Personal overrides and source decisions are projected consistently into views,
search and reminders. Model output must pass complete local validation and retained
page-quote admission before the atomic ingestion transaction.

Actions use body-bound UUID receipts. Preview does not mutate records. Updates
can require the version displayed during preview; conflict-safe undo is atomic.
Backups exclude credentials and profiles and validate archive paths, content hashes,
SQLite structure and relational integrity before an explicit replacement.

The local HTTP boundary checks the actual Host and Origin, with a per-process
session cookie and CSRF token for UI writes. MCP has its own namespace and token,
is initially disabled, and has separate read/write grants. Logs must not include
remote bodies or credentials. Secrets have no stored-value HTTP read route.

Portable components belong to this installation. Do not run dependency setup
programs to populate a build directory. Never change global PATH, another
installation, profiles or registry entries. Windows install/update/uninstall
acceptance belongs in a clean VM; frozen executables may be exercised against an
explicit isolated test directory. Linux OCR dependencies are archive-extracted
using isolated APT configuration; no host installation is required.

Before every commit, stage named files, inspect the diff, run the public-content
gate and an offline secret scan. Before pushing scan all outgoing commit versions.
Before releasing scan the actual frozen contents, nested archives and source
archive, verify checksums and collect upstream license texts and source references.
Do not publish binaries in Git or any local transfer/incident reports.

See [PLAN.md](PLAN.md) for actual platform and account acceptance. A passing mock
protocol check is not a live account, visible desktop, microphone or OS acceptance.
