"""Conservative one-time migration from the beta4 Public database.

The original database and files are never modified. A consistent SQLite snapshot
and the unmodified beta settings remain available in the read-only archive.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta
from contextlib import closing

from .platform.paths import owned_path, reject_links


class MigrationError(RuntimeError):
    """Migration stopped; the old installation can still be used."""


def _read_json(value, default):
    try:
        result = json.loads(value) if value else default
    except (ValueError, TypeError) as exc:
        raise MigrationError('Ungültige gespeicherte JSON-Daten; Beta4 bleibt unverändert.') from exc
    if not isinstance(result, type(default)):
        raise MigrationError('Unerwartete gespeicherte Daten; Beta4 bleibt unverändert.')
    return result


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _rows(con, table):
    return [dict(row) for row in con.execute('SELECT * FROM "' + table + '"')]


def _stop(message):
    raise MigrationError(message + ' Die Beta4-Daten bleiben unverändert; bitte Beta4 weiter verwenden.')


def migrate_legacy(paths):
    """Return a migration report; raise MigrationError on ambiguous active data.

    Called before opening the Core database. Successful staging is published by
    one same-volume directory rename. The retained beta database is the rollback.
    """
    target = owned_path(paths.data / 'core', paths.data)
    source = owned_path(paths.database, paths.data)
    marker = target / 'migration.json'
    if target.exists():
        if marker.is_file():
            return _read_json(marker.read_text(encoding='utf-8'), {})
        if source.exists():
            _stop('Ein Core-Datenordner ohne Migrationsnachweis existiert bereits.')
        return {'state': 'new'}
    if not source.exists():
        return {'state': 'new'}
    if not source.is_file():
        _stop('Die Beta4-Datenbank ist keine Datei.')

    stage = Path(tempfile.mkdtemp(prefix='.core-migration-', dir=paths.data))
    try:
        archive = stage / 'legacy'
        archive.mkdir()
        db_snapshot = archive / 'studycenter-public.sqlite3'
        uri = source.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as src, closing(sqlite3.connect(db_snapshot)) as snap:
            src.execute('PRAGMA query_only=ON')
            src.backup(snap)
        config = reject_links(paths.config / 'settings.json')
        if config.is_file():
            shutil.copyfile(config, archive / 'settings.json')
        con = sqlite3.connect(db_snapshot)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA query_only=ON')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                _stop('Integritätsprüfung der Beta4-Datenbank fehlgeschlagen.')
            if con.execute('PRAGMA user_version').fetchone()[0] != 1:
                _stop('Unbekannte Beta4-Datenbankversion.')
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {'sources','courses','documents','pages','items','item_evidence','decisions','action_receipts','settings','reminder_jobs','class_completions','quest_plans','quest_receipts'}
            if not required.issubset(tables):
                _stop('Unbekanntes Beta4-Datenbankschema.')
            if con.execute('PRAGMA foreign_key_check').fetchone():
                _stop('Beta4-Datenbank enthält ungültige Verknüpfungen.')
            _translate(con, stage, target, paths)
        finally:
            con.close()
        report = {'state':'migrated', 'archive':'legacy/studycenter-public.sqlite3',
                  'notice':'Beta4-Original und Dateien bleiben erhalten. Historie und nicht übertragene Einstellungen sind unter /legacy lesbar; zum Zurückwechseln Beta4 mit dem ursprünglichen Datenordner starten. Deaktivierte Beta4-Erinnerungen bleiben deaktiviert; ihre bisherigen Detailregeln stehen im Archiv. Beta4-Status geplant wird als offener Eintrag mit persönlicher Planung übernommen. Anders als bisher bleibt diese Planung auch nach einem Statuswechsel bestehen und kann in der Erinnerungsregel entfernt werden.'}
        (stage / 'migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.rename(stage, target)
        return report
    except MigrationError:
        raise
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise MigrationError('Migration fehlgeschlagen (' + type(exc).__name__ + '); Beta4-Daten bleiben unverändert. Beta4 weiter verwenden.') from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _translate(old, stage, target, paths):
    from .core.db import Database, encode
    from .core.models import Candidate

    # Explicit decisions influence effective values and have no equivalent Core
    # ledger. The user must retain beta4 behaviour until a faithful bridge exists.
    if old.execute('SELECT 1 FROM decisions LIMIT 1').fetchone():
        _stop('Persönliche Quellenentscheidungen können noch nicht verlustfrei übernommen werden.')
    if old.execute('SELECT 1 FROM reminder_jobs LIMIT 1').fetchone():
        _stop('Aktive Erinnerungsaufträge können noch nicht verlustfrei übernommen werden.')
    if old.execute('SELECT 1 FROM class_completions LIMIT 1').fetchone():
        _stop('Automatische Abschlusszustände können noch nicht verlustfrei übernommen werden.')
    db = Database(stage / 'campusboard.sqlite3')
    sources = _rows(old, 'sources')
    courses = _rows(old, 'courses')
    documents = _rows(old, 'documents')
    items = _rows(old, 'items')
    source_courses = {s['id']: set() for s in sources}
    for course in courses:
        meta = _read_json(course['metadata'], {})
        sid = meta.get('source_id')
        if sid not in source_courses:
            _stop('Eine Kurs-Quellenzuordnung ist nicht eindeutig.')
        source_courses[sid].add(course['id'])
    for item in items:
        if item['source_id'] is not None and item['course_id'] is not None:
            if item['source_id'] not in source_courses:
                _stop('Eine Eintragsquelle fehlt.')
            source_courses[item['source_id']].add(item['course_id'])
    for document in documents:
        meta = _read_json(document['metadata'], {})
        if meta.get('trusted_course_id') is not None:
            if document['source_id'] not in source_courses:
                _stop('Eine Dokumentquelle fehlt.')
            source_courses[document['source_id']].add(meta['trusted_course_id'])
    if any(len(ids) > 1 for ids in source_courses.values()):
        _stop('Eine Beta4-Quelle gehört zu mehreren Kursen; diese Zuordnung lässt sich noch nicht verlustfrei übertragen.')
    reminder_row = old.execute("SELECT value FROM settings WHERE name='reminders'").fetchone()
    reminder_settings = _read_json(reminder_row['value'], {}) if reminder_row else {}
    if reminder_settings.get('enabled', False):
        _stop('Aktivierte Beta4-Erinnerungen können noch nicht verhaltensgleich übernommen werden.')
    with db.connection() as out:
        # Beta4 defaults to disabled while Core defaults to enabled. Preserve the
        # user's disabled state explicitly; old detailed settings stay in archive.
        out.execute("INSERT INTO app_preferences(name,value) VALUES('notifications',?)", (encode({'enabled':False}),))
        for course in courses:
            meta = _read_json(course['metadata'], {})
            source = next((s for s in sources if s['id'] == meta.get('source_id')), None)
            external_id = meta.get('external_id') or None
            if external_id is not None and not isinstance(external_id, str):
                _stop('Eine Kurskennung hat ein unbekanntes Format.')
            if source and source['kind'] == 'teachcenter' and external_id:
                external_id = 'tc:' + external_id
            out.execute('INSERT INTO courses(id,title,semester,group_name,active,external_id) VALUES(?,?,?,?,?,?)',
                        (course['id'], course['title'], meta.get('semester') or '', meta.get('group_name') or meta.get('group') or '', int(meta.get('active', True)), external_id))
        for source in sources:
            meta = _read_json(source['metadata'], {})
            kind = {'web':'public', 'public':'public', 'teachcenter':'teachcenter', 'tugrazonline':'tugrazonline'}.get(source['kind'])
            url = meta.get('url')
            if not kind or not isinstance(url, str) or not url:
                _stop('Eine Quellenart oder Quellenadresse kann nicht eindeutig übertragen werden.')
            sid = source['id']
            status = meta.get('sync_status') or {}
            if not isinstance(status, dict) or not isinstance(status.get('gaps', []), list) or any(not isinstance(g, str) for g in status.get('gaps', [])):
                _stop('Ein gespeicherter Abrufbericht hat ein unbekanntes Format.')
            gaps = status.get('gaps', [])
            old_state = status.get('state', 'idle')
            if old_state == 'complete':
                state = 'success' if status.get('complete') is True and not gaps else 'partial'
            elif old_state in ('running','cancelled','partial'):
                state = 'partial'
            elif old_state in ('failed','login_required'):
                state = old_state
            elif old_state in ('idle','new'):
                state = 'new' if not gaps else 'partial'
            else:
                _stop('Ein gespeicherter Abrufstatus ist unbekannt.')
            messages = list(gaps)
            if old_state in ('running','cancelled'):
                messages.insert(0, 'Beta4-Abruf war beim Übergang nicht abgeschlossen (' + old_state + ').')
            out.execute('INSERT INTO sources(id,course_id,kind,title,url,config,active,state,message,crawl_state,last_attempt,last_success) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (sid, next(iter(source_courses[sid]), None), kind, source['name'], url,
                         encode(meta.get('options', {})), int(meta.get('active', True)),
                         state, '\n'.join(messages), '{}', status.get('started_at'), status.get('finished_at') if state == 'success' else None))
        doc_dir = stage / 'documents'
        doc_dir.mkdir()
        for doc in documents:
            if doc['source_id'] is None:
                _stop('Ein Dokument hat keine Quellenzuordnung.')
            original = doc['original_path']
            if not original:
                _stop('Ein Dokument hat keine gespeicherte Originaldatei.')
            file = owned_path(Path(original), paths.documents)
            if not file.is_file() or _digest(file) != doc['hash']:
                _stop('Ein Dokumentoriginal fehlt oder stimmt nicht mit seinem Hash überein.')
            destname = str(doc['id']) + '-' + file.name
            shutil.copyfile(file, doc_dir / destname)
            if _digest(doc_dir / destname) != doc['hash']:
                _stop('Dokumentkopie konnte nicht geprüft werden.')
            meta = _read_json(doc['metadata'], {})
            pages = []
            for page in old.execute('SELECT * FROM pages WHERE document_id=? ORDER BY page', (doc['id'],)):
                pages.append(_read_json(page['metadata'], {}) | {'page':page['page'], 'text':page['text']})
            out.execute('''INSERT INTO documents(id,source_id,url,title,mime,hash,published_at,fetched_at,original_path,pages,processing_state,analysis_state,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (doc['id'],doc['source_id'],doc['url'],doc['title'],doc['mime'],doc['hash'],doc['published_at'],doc['fetched_at'],str(target / 'documents' / destname),encode(pages),meta.get('processing_state','pending'),meta.get('analysis_state','pending'),str(doc['revision'])))
        for item in items:
            payload = _read_json(item['payload'], {})
            overrides = _read_json(item['overrides'], {})
            if payload.get('linked_source_id'):
                _stop('Eine persönliche Aufgabe hat eine verknüpfte Quellenfrist, deren Verhalten noch nicht übertragen werden kann.')
            identity = item['identity']
            own = identity.startswith('personal:')
            if own:
                identity = 'note:beta4:' + identity.removeprefix('personal:')
                if payload.get('kind') == 'note':
                    payload['kind'] = 'task'
            for value in (payload, overrides):
                if value.get('body') and value.get('description') and value['body'] != value['description']:
                    _stop('Ein Eintrag enthält zwei verschiedene Textfelder.')
                if value.get('body'):
                    value['description'] = value['body']
                value.pop('body', None)
            pinned = int(bool(overrides.get('pinned',payload.get('pinned',False))))
            ignored = int(bool(item['deleted']) or item['status']=='ignored' or bool(overrides.get('ignored', payload.get('ignored',False))))
            for field in ('pinned','ignored','course_id'):
                payload.pop(field, None)
                overrides.pop(field, None)
            if own:
                # This label denotes the user's own note, never source evidence.
                payload.update({k:v for k,v in {'quote':'Eigene Notiz','page':1,'anchor':'note'}.items() if k not in payload})
            elif any(k not in payload for k in ('quote','page','anchor')):
                proofs = old.execute('SELECT quote,page,anchor FROM item_evidence WHERE item_id=? AND current=1', (item['id'],)).fetchall()
                choices = {(p['quote'],p['page'],p['anchor']) for p in proofs}
                if len(choices) != 1:
                    _stop('Ein Quellenbeleg für die Korrektur ist nicht eindeutig.')
                quote, page, anchor = next(iter(choices))
                for field, value in (('quote',quote),('page',page),('anchor',anchor)):
                    payload.setdefault(field,value)
            try:
                payload = Candidate.model_validate(payload).model_dump(mode='json')
                Candidate.model_validate(payload | overrides)
            except ValueError:
                _stop('Ein Eintrag oder eine persönliche Korrektur kann im neuen Kern nicht bearbeitet werden.')
            if not own:
                identity = _core_identity(old, out, item, encode)
            if item['status'] not in ('open','planned','done','ignored'):
                _stop('Ein persönlicher Status ist unbekannt.')
            out.execute('''INSERT INTO items(id,course_id,identity,payload,status,ignored,pinned,overrides,created_at,changed_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (item['id'],item['course_id'],identity,encode(payload),('open' if item['status'] in ('ignored','planned') else item['status']),ignored,pinned,encode(overrides),item['created_at'],item['updated_at']))
            if item['status'] == 'planned':
                # Public explicitly projects planned as personal_event. Core's
                # personal choice is independent of its work-progress status.
                out.execute('INSERT INTO item_preferences(item_id,value) VALUES(?,?)',
                            (item['id'], encode({'personal':True})))
        for proof in _rows(old, 'item_evidence'):
            out.execute('INSERT INTO item_evidence(id,item_id,document_id,page,anchor,quote,payload,current) VALUES(?,?,?,?,?,?,?,?)', tuple(proof[k] for k in ('id','item_id','document_id','page','anchor','quote','payload','current')))
        for quest in _rows(old, 'quest_plans'):
            data = _read_json(quest['data'], {})
            try:
                start = date.fromisoformat(data['start_date'])
                end = (start + timedelta(days=int(data['horizon']) - 1)).isoformat()
                steps = data['steps']
                snapshots = data.get('source_snapshots') or ([data['source_snapshot']] if data.get('source_snapshot') else [])
                if not isinstance(steps, list) or not isinstance(snapshots, list):
                    raise ValueError()
                if quest['state'] == 'discarded':
                    # Discarded plans are accessible in the legacy archive.
                    continue
                translated_sources = _quest_sources(old, db, out, snapshots, data, stage, target)
                if quest['state'] not in ('draft','active','archived'):
                    raise ValueError()
                out.execute('''INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,sources,steps,version,created_at,updated_at,planning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (quest['id'],quest['request_id'],quest['input_hash'],data['title'],data['mode'],data['horizon'],data['daily_minutes'],data['start_date'],end,quest['state'],encode(translated_sources),encode(steps),quest['version'],quest['created_at'],quest['updated_at'],data['planning']))
            except (KeyError, TypeError, ValueError):
                _stop('Ein Quest-Plan kann nicht eindeutig übertragen werden.')
        if out.execute('PRAGMA foreign_key_check').fetchone():
            _stop('Die übertragenen Daten haben ungültige Verknüpfungen.')


def _quest_sources(old, db, out, snapshots, data, stage, target):
    """Translate only snapshots still matching all retained beta4 versions.

    Core has a different change-detection format. Reconstruct its native snapshot
    from the same verified local evidence, rejecting any mismatch or ambiguity.
    """
    from .core.db import encode
    from .core.quests import QuestService

    ids = data.get('item_ids') or ([data['item_id']] if data.get('item_id') else [])
    if not snapshots and not ids and not data.get('document_ids'):
        return []
    if len(snapshots) != len(ids) or any(not isinstance(s, dict) for s in snapshots):
        _stop('Eine Quest mit Quellenbindung kann noch nicht verlustfrei übertragen werden.')
    if [s.get('item_id') for s in snapshots] != ids or any(s.get('linked_source_id') for s in snapshots):
        _stop('Eine Quest mit Quellenbindung kann noch nicht verlustfrei übertragen werden.')
    service = QuestService(db, None, None)
    current = {i['id']: i for i in db._items(False, out)}
    selected = []
    for snapshot in snapshots:
        item_id = snapshot['item_id']
        item = current.get(item_id)
        row = old.execute('SELECT version FROM items WHERE id=?', (item_id,)).fetchone()
        if not item or not row or row['version'] != snapshot.get('item_version') or item['data'].get('title') != snapshot.get('item_title'):
            _stop('Eine Quest-Quelle wurde nach ihrer Planung geändert; Beta4 zur Prüfung verwenden.')
        for document in snapshot.get('documents', []):
            row = old.execute('SELECT * FROM documents WHERE id=?', (document.get('id'),)).fetchone()
            if not row:
                _stop('Ein Quest-Dokument fehlt.')
            pages = []
            for p in old.execute('SELECT * FROM pages WHERE document_id=? ORDER BY page', (row['id'],)):
                pages.append({'page':p['page'], 'text':p['text'], 'gaps':_read_json(p['metadata'], {}).get('gaps', [])})
            page_hash = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            meta = _read_json(row['metadata'], {})
            latest = old.execute("SELECT id FROM documents WHERE source_id IS ? AND url=? AND json_extract(metadata,'$.trusted_course_id') IS ? ORDER BY id DESC LIMIT 1", (row['source_id'],row['url'],meta.get('trusted_course_id'))).fetchone()
            if (row['hash'] != document.get('hash') or row['revision'] != document.get('revision')
                    or page_hash != document.get('page_hash') or (latest['id'] if latest else None) != document.get('latest_id')):
                _stop('Ein Quest-Dokument wurde nach der Planung geändert; Beta4 zur Prüfung verwenden.')
        selected.append(item)
    if not selected:
        _stop('Direkt ausgewählte Quest-Dokumente können noch nicht verlustfrei übertragen werden.')
    # Core's snapshot includes file timestamps. Collect those from the staged
    # copies; renaming the staging directory leaves those timestamps unchanged.
    docpaths = [(r['id'], r['original_path']) for r in out.execute('SELECT id,original_path FROM documents')]
    try:
        for docid, original in docpaths:
            out.execute('UPDATE documents SET original_path=? WHERE id=?', (str(stage/'documents'/Path(original).name),docid))
        contexts, _ = service._document_context(selected, out)
        result = service._sources(selected, out)
        for snapshot, source in zip(snapshots, result):
            context = contexts[source['id']]
            if {d['id'] for d in context['documents']} != {d['id'] for d in snapshot.get('documents', [])}:
                _stop('Eine Quest-Dokumentauswahl unterscheidet sich im neuen Kern; Beta4 weiter verwenden.')
            source.update(context)
        return result
    finally:
        for docid, original in docpaths:
            out.execute('UPDATE documents SET original_path=? WHERE id=?', (original,docid))


def _core_identity(old, out, item, encode):
    """Re-key to the shared ingester from explicit current evidence, not titles."""
    rows = old.execute("""SELECT e.payload,e.anchor,e.quote,e.page,d.id AS document_id,d.url,d.source_id FROM item_evidence e
        JOIN documents d ON d.id=e.document_id WHERE e.item_id=? AND e.current=1""", (item['id'],)).fetchall()
    if not rows:
        _stop('Für einen Quelleneintrag fehlt ein aktueller Beleg zur eindeutigen Zuordnung.')
    identities = set()
    for row in rows:
        source = out.execute('SELECT s.course_id,c.semester,c.group_name FROM sources s LEFT JOIN courses c ON c.id=s.course_id WHERE s.id=?', (row['source_id'],)).fetchone()
        if not source or source['course_id'] != item['course_id']:
            _stop('Eine Eintragsquelle kann ihrem Kurs nicht eindeutig zugeordnet werden.')
        page = old.execute('SELECT text FROM pages WHERE document_id=? AND page=?', (row['document_id'],row['page'])).fetchone()
        if not page or not row['quote'] or row['quote'] not in page['text']:
            _stop('Ein gespeichertes Belegzitat fehlt auf der zugehörigen Seite.')
        claim = _read_json(row['payload'], {})
        if claim.get('identity_scope', 'document') not in ('document','course'):
            _stop('Ein Beleg hat einen unbekannten Identitätsbereich.')
        kind = claim.get('kind')
        if kind not in ('task','exam','event','registration','notice'):
            _stop('Ein Quelleneintrag hat eine unbekannte Belegart.')
        context = [source['course_id'], claim.get('semester') or source['semester'],
                   claim.get('group') or source['group_name'], kind]
        if claim.get('identity_scope', 'document') == 'course' and source['course_id'] is not None:
            key = claim.get('identity_key')
            if not isinstance(key, str) or not key.strip():
                _stop('Eine Kurskennung im Beleg fehlt.')
            context.append(key.casefold().strip())
        else:
            key = claim.get('identity_key') or row['anchor']
            if not key or not row['url']:
                _stop('Eine Dokumentkennung im Beleg fehlt.')
            context.extend([row['url'], key])
        identities.add(hashlib.sha256(encode(context).encode()).hexdigest())
    if len(identities) != 1:
        _stop('Mehrere aktuelle Belege ergeben unterschiedliche Eintragsidentitäten.')
    return next(iter(identities))
