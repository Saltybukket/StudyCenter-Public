"""Explicit, offline import of a validated StudyCenter-ICE format-1 backup.

Only a new data directory can be published. The backup is never modified.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from contextlib import closing

from .platform.paths import AppPaths, reject_links, restrict_permissions

REQUIRED = {'courses', 'sources', 'documents', 'items', 'item_evidence', 'sync_runs'}
OPTIONAL = {'assistant_requests', 'course_aliases', 'personal_reminders', 'app_preferences',
            'item_preferences', 'reminder_jobs', 'notification_reads', 'google_events',
            'class_completions', 'quests'}
ORDER = ('courses', 'sources', 'documents', 'items', 'item_evidence', 'sync_runs',
         'assistant_requests', 'course_aliases', 'personal_reminders', 'app_preferences',
         'item_preferences', 'reminder_jobs', 'notification_reads', 'google_events',
         'class_completions', 'quests')
MAX_SIZE = 2 * 1024**3
SECRET = re.compile(r'password|passwd|secret|token|cookie|credential|authorization|api[_-]?key', re.I)
SECRET_URL = re.compile(r'(?:[?&](?:token|password|api_key)=|://[^/]+:[^/]+@)', re.I)


class IceImportError(RuntimeError):
    """The import stopped without publishing a destination."""


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _contains_secret(value):
    if isinstance(value, dict):
        return any(SECRET.search(str(k)) or _contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(v) for v in value)
    return isinstance(value, str) and bool(SECRET_URL.search(value))


def _publish(stage, target):
    # Windows rename rejects an existing destination; Linux requires NOREPLACE.
    if os.name == 'nt':
        os.rename(stage, target)
    elif sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(libc, 'renameat2'):
            raise IceImportError('Atomare Veröffentlichung ohne Überschreiben wird nicht unterstützt.')
        rc = libc.renameat2(-100, os.fsencode(stage), -100, os.fsencode(target), 1)
        if rc:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    else:
        raise IceImportError('Atomare Veröffentlichung wird auf diesem System nicht unterstützt.')


def import_ice(backup, destination):
    """Import an ICE backup into a nonexistent directory and return a count report."""
    try:
        backup = reject_links(Path(backup).expanduser())
        target = reject_links(Path(destination).expanduser())
        parent = reject_links(target.parent)
    except (ValueError, OSError) as exc:
        raise IceImportError('Backup- oder Zielpfad ist ungültig.') from exc
    if not backup.is_file() or not parent.is_dir() or target.exists():
        raise IceImportError('Backup oder Ziel ungültig: Ziel muss ein neuer, noch nicht vorhandener Ordner sein.')
    if backup.resolve().is_relative_to(target.resolve()):
        raise IceImportError('Backup darf nicht im Ziel liegen.')
    stage = Path(tempfile.mkdtemp(prefix='.studycenter-ice-import-', dir=parent))
    try:
        # Protect the empty staging directory before any private data is extracted.
        try:
            restrict_permissions(stage)
        except OSError as exc:
            raise IceImportError('Der temporäre Importordner konnte nicht ausreichend geschützt werden.') from exc
        backup_sha256 = _hash(backup)
        # AppPaths assigns a fresh Public instance and host identity, never ICE credentials.
        paths = AppPaths.create(stage)
        core = paths.data / 'core'
        core.mkdir()
        extracted = stage / '.ice-validation'
        extracted.mkdir()
        manifest, manifest_raw = _extract(backup, extracted)
        from .core.db import Database
        output = Database(core / 'campusboard.sqlite3')
        counts, changes = _copy(extracted, manifest, output, target / 'core')
        if _hash(backup) != backup_sha256:
            raise IceImportError('Das Backup wurde während des Imports geändert; erneut mit einer stabilen Kopie versuchen.')
        shutil.rmtree(extracted)
        (core / 'ice-source-manifest.json').write_bytes(manifest_raw)
        receipt = {'format': 'StudyCenter-Public-ICE-import', 'version': 1,
                   'backup_sha256': backup_sha256, 'source_manifest': 'ice-source-manifest.json',
                   'source_app_version': manifest['app_version'], 'source_created_at': manifest['created_at'],
                   'transformations': changes}
        (core / 'ice-import.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'state': 'ice_imported', 'counts': counts, 'receipt': 'ice-import.json',
                  'notice': 'ICE-Backup importiert. Google-Spiegelung ist deaktiviert; Anmeldungen wurden nicht übernommen.'}
        (core / 'migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        _publish(stage, target)
        return report
    except IceImportError:
        raise
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile, UnicodeError) as exc:
        raise IceImportError('ICE-Import fehlgeschlagen (' + type(exc).__name__ + '); das Backup bleibt unverändert.') from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _extract(backup, dest):
    with zipfile.ZipFile(backup) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)) or len(names) > 20000 or sum(m.file_size for m in members) > MAX_SIZE:
            raise IceImportError('Backup enthält doppelte Dateien oder ist zu groß.')
        for m in members:
            if (m.is_dir() or stat.S_ISLNK(m.external_attr >> 16) or
                not (m.filename in ('manifest.json', 'studycenter_ice.sqlite3') or
                     re.fullmatch(r'documents/[a-f0-9]{64}\.[a-z0-9]+', m.filename))):
                raise IceImportError('Ungültiger Archivpfad.')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 4 * 1024**2:
            raise IceImportError('Backup-Manifest fehlt oder ist zu groß.')
        manifest_raw = archive.read('manifest.json')
        manifest = json.loads(manifest_raw)
        if (not isinstance(manifest, dict) or manifest.get('format') != 'StudyCenter-ICE' or
            manifest.get('version') != 1 or manifest.get('schema_version') != 1 or
            not isinstance(manifest.get('app_version'), str) or not manifest['app_version'] or
            not isinstance(manifest.get('created_at'), str) or not manifest['created_at'] or
            _contains_secret(manifest) or
            not isinstance(manifest.get('files'), dict) or
            set(manifest['files']) != set(names) - {'manifest.json'} or
            'studycenter_ice.sqlite3' not in names):
            raise IceImportError('Backup-Version oder Manifest ist ungültig.')
        for name, info in manifest['files'].items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, target.open('xb') as out:
                shutil.copyfileobj(src, out)
            if target.stat().st_size != info['size'] or _hash(target) != info['sha256']:
                raise IceImportError('Prüfsumme im ICE-Backup stimmt nicht.')
        return manifest, manifest_raw


def _copy(extracted, manifest, output, target_core):
    source = extracted / 'studycenter_ice.sqlite3'
    referenced = set()
    changes = {'google_calendar': {'row_present': False, 'fields': {
        'enabled': {'present_before': False, 'after': False},
        'consent': {'present_before': False, 'after': False}}},
        'documents_original_path': []}
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old:
        old.row_factory = sqlite3.Row
        old.execute('PRAGMA query_only=ON')
        old.execute('PRAGMA trusted_schema=OFF')
        if old.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or old.execute('PRAGMA foreign_key_check').fetchone():
            raise IceImportError('Integritätsprüfung der ICE-Datenbank fehlgeschlagen.')
        objects = old.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        tables = {r['name'] for r in objects if r['type'] == 'table'}
        if (old.execute('PRAGMA user_version').fetchone()[0] != 1 or not REQUIRED <= tables or
            not tables <= REQUIRED | OPTIONAL or
            any(r['type'] not in ('table', 'index') or 'VIRTUAL' in (r['sql'] or '').upper() for r in objects)):
            raise IceImportError('Nicht unterstütztes ICE-Datenbankschema.')
        counts = {t: old.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        if counts != manifest.get('counts'):
            raise IceImportError('Datensatzanzahl stimmt nicht mit dem Manifest überein.')
        for row in old.execute('SELECT config,url FROM sources'):
            if _contains_secret(json.loads(row['config'])) or _contains_secret(row['url']):
                raise IceImportError('Quellenkonfiguration enthält mögliche Zugangsdaten.')
        with output.connection() as new:
            for table in tables:
                cols = lambda con: [tuple(r[1:]) for r in con.execute(f'PRAGMA table_xinfo("{table}")')]
                if cols(old) != cols(new):
                    raise IceImportError('Nicht unterstützte ICE-Tabellenspalten: ' + table)
            doc_dir = Path(output.path).parent / 'documents'
            doc_dir.mkdir()
            for table in ORDER:
                if table not in tables:
                    continue
                columns = [r[1] for r in old.execute(f'PRAGMA table_info("{table}")')]
                sql = f'INSERT INTO "{table}" (' + ','.join('"'+c+'"' for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
                for row in old.execute(f'SELECT * FROM "{table}"'):
                    values = dict(row)
                    if table == 'documents':
                        name = values['original_path']
                        if name not in manifest['files'] or not re.fullmatch(r'documents/[a-f0-9]{64}\.[a-z0-9]+', name) or _hash(extracted / name) != values['hash']:
                            raise IceImportError('Ein Dokumentoriginal fehlt oder ist beschädigt.')
                        referenced.add(name)
                        changes['documents_original_path'].append(
                            {'document_id': values['id'], 'before': name,
                             'after': 'core/documents/' + Path(name).name})
                        dest = doc_dir / Path(name).name
                        if not dest.exists():
                            shutil.copyfile(extracted / name, dest)
                        if _hash(dest) != values['hash']:
                            raise IceImportError('Dokumentkopie ist beschädigt.')
                        values['original_path'] = str(target_core / 'documents' / dest.name)
                    if table == 'app_preferences':
                        options = json.loads(values['value'])
                        if SECRET.search(values['name']) or _contains_secret(options):
                            raise IceImportError('Eine Einstellung enthält mögliche Zugangsdaten.')
                        if values['name'] == 'google_calendar':
                            if not isinstance(options, dict):
                                raise IceImportError('Google-Einstellungen sind ungültig.')
                            changes['google_calendar']['row_present'] = True
                            for field in ('enabled', 'consent'):
                                record = changes['google_calendar']['fields'][field]
                                record['present_before'] = field in options
                                if field in options:
                                    record['before'] = options[field]
                            options.update(enabled=False, consent=False)
                            values['value'] = json.dumps(options, ensure_ascii=False)
                    new.execute(sql, [values[c] for c in columns])
            if referenced != set(manifest['files']) - {'studycenter_ice.sqlite3'}:
                raise IceImportError('Das Backup enthält ein nicht zugeordnetes Dokumentoriginal; Import abgebrochen.')
            if new.execute('PRAGMA foreign_key_check').fetchone() or new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise IceImportError('Prüfung des importierten Bestands fehlgeschlagen.')
    return counts, changes
