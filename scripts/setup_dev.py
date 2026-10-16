"""Developer entry point for the same standalone setup shipped in slim releases."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('studycenter_public_setup', ROOT / 'src/studycenter_public/platform/setup.py')
_engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_engine)
# Compatibility for callers checking the existing developer environment boundary.
environment_python = _engine.environment_python
environment_usable = _engine.environment_usable
preserve_environment = _engine.preserve_environment
regular_directory = _engine.regular_directory


def main(argv=None):
    return _engine.main(['--root', str(ROOT), '--source', str(ROOT), *(sys.argv[1:] if argv is None else argv)])


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, _engine.subprocess.CalledProcessError) as error:
        print(f'Public setup failed: {error}', file=sys.stderr)
        raise SystemExit(1)
