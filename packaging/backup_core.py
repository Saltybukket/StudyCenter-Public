"""Back up the stopped installed Public core before replacing its environment."""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import portalocker
from studycenter_public.core.backup import create_backup
from studycenter_public.platform.paths import owned_path, reject_links


def backup(data):
    data = reject_links(Path(data).absolute())
    core = reject_links(data/'core')
    database = owned_path(core/'campusboard.sqlite3', core)
    if not database.is_file():
        raise ValueError('The existing core database is missing')
    reject_links(core/'backups')
    documents = reject_links(core/'documents')
    # Fail before snapshotting if any referenced original traverses a link or
    # escapes the owned document store. create_backup verifies every hash.
    lock = owned_path(data/'instance.lock', data)
    with portalocker.Lock(str(lock), timeout=0):
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as con:
            con.execute('PRAGMA query_only=ON')
            for original, in con.execute('SELECT original_path FROM documents'):
                owned_path(Path(original), documents)
        return create_backup(SimpleNamespace(path=database), core)


if __name__ == '__main__':
    try:
        print(backup(sys.argv[1]))
    except (OSError, ValueError, sqlite3.Error, TimeoutError, portalocker.exceptions.LockException) as error:
        print('Core backup failed: '+str(error), file=sys.stderr)
        raise SystemExit(1)
