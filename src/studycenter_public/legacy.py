"""Read-only browser for the retained beta4 snapshot and source originals."""
from html import escape
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .platform.paths import owned_path

TABLES = ('courses','sources','documents','pages','items','item_evidence','decisions',
          'action_receipts','settings','reminder_jobs','class_completions','quest_plans','quest_receipts')


def create_legacy_router(paths):
    router = APIRouter()
    archive = paths.data / 'core' / 'legacy' / 'studycenter-public.sqlite3'

    def connect():
        path = owned_path(archive, paths.data)
        if not path.is_file():
            raise HTTPException(404, 'Kein Beta4-Archiv vorhanden.')
        con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        con.execute('PRAGMA trusted_schema=OFF')
        return con

    @router.get('/legacy', response_class=HTMLResponse)
    def index(table: str = 'items', offset: int = 0):
        if table not in TABLES or offset < 0 or offset > 1_000_000:
            raise HTTPException(400, 'Ungültige Archivansicht.')
        with closing(connect()) as con:
            rows = con.execute('SELECT rowid AS _archive_rowid_, * FROM "' + table + '" LIMIT 101 OFFSET ?', (offset,)).fetchall()
        links = ' · '.join('<a href="/legacy?table=' + t + '">' + escape(t) + '</a>' for t in TABLES) + ' · <a href="/legacy/settings">Beta4-Einstellungen</a>'
        body = []
        for row in rows[:100]:
            body.append('<article><dl>')
            for key in row.keys():
                if key == '_archive_rowid_':
                    continue
                value = str(row[key]) if row[key] is not None else 'NULL'
                extra = ''
                if len(value) > 50000:
                    value = value[:50000] + ' … [gekürzt]'
                    from urllib.parse import urlencode
                    extra = '<a href="/legacy/field?' + escape(urlencode({'table':table,'row':row['_archive_rowid_'],'column':key}), quote=True) + '">Vollständiges Feld anzeigen</a>'
                body.append('<dt>' + escape(key) + '</dt><dd><pre>' + escape(value) + '</pre>' + extra + '</dd>')
            if table == 'documents':
                body.append('<dd><a href="/legacy/documents/' + str(int(row['id'])) + '">Original herunterladen</a></dd>')
            body.append('</dl></article>')
        next_link = '<a href="/legacy?table=' + table + '&amp;offset=' + str(offset+100) + '">Weitere Datensätze</a>' if len(rows) > 100 else ''
        return HTMLResponse('<!doctype html><html lang="de"><meta charset="utf-8"><title>Beta4-Archiv</title><body><h1>Beta4-Archiv (nur lesend)</h1><p>Der ursprüngliche Stand bleibt erhalten. Historische Aktionen sind hier einsehbar, können aber in dieser Ansicht nicht rückgängig gemacht werden.</p><nav>' + links + '</nav><h2>' + escape(table) + '</h2>' + ''.join(body) + next_link + '</body></html>', headers={'Cache-Control':'no-store', 'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'", 'X-Content-Type-Options':'nosniff'})

    @router.get('/legacy/field', response_class=HTMLResponse)
    def field(table: str, row: int, column: str):
        if table not in TABLES or row < 1:
            raise HTTPException(400, 'Ungültiges Archivfeld.')
        with closing(connect()) as con:
            columns = {r['name'] for r in con.execute('PRAGMA table_info("' + table + '")')}
            if column not in columns:
                raise HTTPException(400, 'Ungültiges Archivfeld.')
            # Only validated schema column names are interpolated.
            quoted = column.replace('"', '""')
            result = con.execute('SELECT "' + quoted + '" FROM "' + table + '" WHERE rowid=?', (row,)).fetchone()
        if not result:
            raise HTTPException(404, 'Archivfeld nicht vorhanden.')
        value = '' if result[0] is None else str(result[0])
        return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Archivfeld</title><h1>Vollständiges Archivfeld</h1><pre>' + escape(value) + '</pre>', headers={'Cache-Control':'no-store', 'Content-Security-Policy':"default-src 'none'", 'X-Content-Type-Options':'nosniff'})

    @router.get('/legacy/settings', response_class=HTMLResponse)
    def settings():
        path = owned_path(paths.data / 'core' / 'legacy' / 'settings.json', paths.data)
        if not path.is_file():
            raise HTTPException(404, 'Keine gespeicherte Beta4-Einstellungsdatei vorhanden.')
        return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Beta4-Einstellungen</title><h1>Beta4-Einstellungen (nur lesend)</h1><pre>' + escape(path.read_text(encoding='utf-8')) + '</pre>', headers={'Cache-Control':'no-store', 'Content-Security-Policy':"default-src 'none'", 'X-Content-Type-Options':'nosniff'})

    @router.get('/legacy/documents/{document_id}')
    def original(document_id: int):
        with closing(connect()) as con:
            row = con.execute('SELECT original_path,hash FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row or not row['original_path']:
            raise HTTPException(404, 'Original nicht vorhanden.')
        try:
            file = owned_path(paths.data / 'core' / 'documents' / (str(document_id) + '-' + Path(row['original_path']).name), paths.data / 'core' / 'documents')
            if not file.is_file():
                raise HTTPException(404, 'Original nicht vorhanden.')
            with file.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != row['hash']:
                raise HTTPException(409, 'Original stimmt nicht mit dem gespeicherten Hash überein.')
        except (ValueError, OSError):
            raise HTTPException(404, 'Original nicht verfügbar.') from None
        return FileResponse(file, filename=file.name, media_type='application/octet-stream', headers={'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store'})

    return router
