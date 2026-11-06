"""Run from the Public checkout with its installed dependencies."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from studycenter_public.ice_migration import IceImportError, import_ice


def main():
    parser = argparse.ArgumentParser(description='ICE-Backup offline in einen neuen Public-Datenordner importieren.')
    parser.add_argument('--backup', required=True, type=Path, help='ICE-Backup-ZIP')
    parser.add_argument('--destination', required=True, type=Path, help='Neuer, noch nicht vorhandener Datenordner')
    args = parser.parse_args()
    try:
        import_ice(args.backup, args.destination)
    except IceImportError as error:
        print(str(error), file=sys.stderr)
        return 1
    print('ICE-Import abgeschlossen. Google-Spiegelung deaktiviert; Anmeldungen nicht übernommen.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
