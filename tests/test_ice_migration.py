import hashlib
import json
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studycenter_public.core.models import Candidate
from studycenter_public.ice_migration import IceImportError, import_ice
from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.product import create_app


# Frozen format-1 schema from ICE db.py. The fixture never imports an ICE runtime.
ICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, semester TEXT NOT NULL DEFAULT '',
 group_name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
 external_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, course_id INTEGER REFERENCES courses(id), kind TEXT NOT NULL,
 title TEXT NOT NULL, url TEXT NOT NULL, config TEXT NOT NULL DEFAULT '{}',
 active INTEGER NOT NULL DEFAULT 1, last_attempt TEXT, last_success TEXT,
 state TEXT NOT NULL DEFAULT 'new', message TEXT NOT NULL DEFAULT '',
 crawl_state TEXT NOT NULL DEFAULT '{}', UNIQUE(kind,url));
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
 url TEXT NOT NULL, title TEXT NOT NULL, mime TEXT NOT NULL, hash TEXT NOT NULL,
 published_at TEXT, fetched_at TEXT NOT NULL, original_path TEXT NOT NULL,
 pages TEXT NOT NULL DEFAULT '[]', processing_state TEXT NOT NULL DEFAULT 'pending',
 analysis_state TEXT NOT NULL DEFAULT 'pending', analysis_at TEXT,
 error TEXT NOT NULL DEFAULT '', progress TEXT NOT NULL DEFAULT '{}',
 UNIQUE(source_id,url,hash));
CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY, course_id INTEGER REFERENCES courses(id), identity TEXT NOT NULL UNIQUE,
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', ignored INTEGER NOT NULL DEFAULT 0,
 pinned INTEGER NOT NULL DEFAULT 0, overrides TEXT NOT NULL DEFAULT '{}',
 conflict INTEGER NOT NULL DEFAULT 0, history TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, changed_at TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS item_evidence (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), page INTEGER NOT NULL,
 anchor TEXT NOT NULL, quote TEXT NOT NULL, payload TEXT NOT NULL,
 current INTEGER NOT NULL DEFAULT 1, UNIQUE(item_id,document_id,page,anchor,quote));
CREATE TABLE IF NOT EXISTS sync_runs (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, auto_day TEXT UNIQUE,
 started_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL DEFAULT 'running',
 results TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS assistant_requests (
 id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, input TEXT NOT NULL,
 state TEXT NOT NULL, action TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS course_aliases (
 alias TEXT NOT NULL,course_id INTEGER NOT NULL REFERENCES courses(id),
 PRIMARY KEY(alias,course_id));
CREATE TABLE IF NOT EXISTS personal_reminders (
 item_id INTEGER PRIMARY KEY REFERENCES items(id),due_at TEXT NOT NULL,
 request_id TEXT NOT NULL,delivered_at TEXT);
CREATE TABLE IF NOT EXISTS app_preferences (name TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS item_preferences (item_id INTEGER PRIMARY KEY REFERENCES items(id),value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminder_jobs (
 id TEXT PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id),target_at TEXT NOT NULL,
 due_at TEXT NOT NULL,rule TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',updated_at TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS notification_reads (
 job_id TEXT PRIMARY KEY REFERENCES reminder_jobs(id),read_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS google_events (
 calendar_id TEXT NOT NULL,identity TEXT NOT NULL,role TEXT NOT NULL,event_id TEXT NOT NULL,
 payload TEXT NOT NULL,hash TEXT NOT NULL,etag TEXT,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(calendar_id,identity,role));
PRAGMA user_version=1;
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def backup(tmp_path, *, secret=False, bad_document=False, include_quest=False):
    db = tmp_path / 'synthetic.sqlite3'
    db.unlink(missing_ok=True)
    raw = b'synthetic document original'
    sha = digest(raw)
    document = f'documents/{sha}.pdf'
    with closing(sqlite3.connect(db)) as con, con:
        con.executescript(ICE_SCHEMA)
        for column in ('revision', 'repository_path', 'source_url'):
            con.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE documents ADD COLUMN mail_metadata TEXT NOT NULL DEFAULT '{}'")
        if include_quest:
            con.execute("""CREATE TABLE quests (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, title TEXT NOT NULL, mode TEXT NOT NULL, horizon INTEGER NOT NULL, daily_minutes INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft', sources TEXT NOT NULL DEFAULT '[]', steps TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, planning TEXT NOT NULL DEFAULT 'paced')""")
            con.execute("INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,sources,steps,version,created_at,updated_at) VALUES('q','req','hash','Plan','manual',2,20,'2026-01-01','2026-01-02','draft','[]','[]',1,'2026-01-01','2026-01-01')")
        con.execute("INSERT INTO courses(id,title) VALUES(8,'Synthetic course')")
        con.execute("INSERT INTO sources(id,course_id,kind,title,url,config) VALUES(3,8,'public','Source','https://example.org',?)", (json.dumps({'token':'not-to-copy'} if secret else {'mode':'manual'}),))
        con.execute("INSERT INTO documents(id,source_id,url,title,mime,hash,fetched_at,original_path,pages,revision) VALUES(11,3,'https://example.org/a','Document','application/pdf',?,'2026-01-01',?,'[]','rev1')", (sha, document))
        payload = Candidate(title="Synthetic note", kind="task", quote="Own note", page=1, anchor="note").model_dump_json()
        con.execute("INSERT INTO items(id,course_id,identity,payload,status,pinned,overrides,history,created_at,changed_at,seen) VALUES(17,8,'note:manual:123',?,'done',1,'{\"description\":\"own note\"}','[]','2026-01-01','2026-01-02',1)", (payload,))
        con.execute("INSERT INTO item_evidence(id,item_id,document_id,page,anchor,quote,payload,current) VALUES(19,17,11,1,'p1','quote','{}',1)")
        con.execute("INSERT INTO item_preferences VALUES(17,'{\"personal\":true}')")
        con.execute("INSERT INTO app_preferences VALUES('google_calendar','{\"enabled\":true,\"consent\":true,\"minute_marker\":false}')")
        con.execute("INSERT INTO reminder_jobs(id,item_id,target_at,due_at,rule,state,updated_at) VALUES('job',17,'2026-01-01','2026-01-01','{}','delivered','2026-01-01')")
        con.execute("INSERT INTO notification_reads VALUES('job','2026-01-02')")
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t:con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    contents = {'studycenter_ice.sqlite3': db.read_bytes(), document: raw if not bad_document else b'corrupt'}
    # This matches the manifest written by ICE create_backup, without relying on ICE at test time.
    manifest = {'format':'StudyCenter-ICE', 'version':1, 'app_version':'0.1.0',
                'schema_version':1, 'created_at':'2026-01-01T00:00:00+00:00', 'counts':counts,
                'excluded':['OAuth / TU sessions', 'browser profiles', 'passwords', 'local settings / credentials', 'repository caches'],
                'files':{name:{'size':len(data), 'sha256':digest(data)} for name,data in contents.items()}}
    if bad_document:
        manifest['files'][document]['sha256'] = sha
    path = tmp_path / 'backup.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        for name,data in contents.items(): z.writestr(name,data)
    return path


def test_rich_import_and_real_public_startup(tmp_path):
    source = backup(tmp_path)
    before = digest(source.read_bytes())
    target = tmp_path / 'new-public'
    result = import_ice(source, target)
    assert digest(source.read_bytes()) == before
    assert result['counts']['items'] == 1
    receipt = json.loads((target / 'core' / 'ice-import.json').read_text(encoding='utf-8'))
    assert receipt['backup_sha256'] == before
    assert receipt['source_app_version'] == '0.1.0'
    assert receipt['source_created_at'] == '2026-01-01T00:00:00+00:00'
    with zipfile.ZipFile(source) as archive:
        assert (target / 'core' / 'ice-source-manifest.json').read_bytes() == archive.read('manifest.json')
    google = receipt['transformations']['google_calendar']
    assert google['row_present'] is True
    assert google['fields'] == {'enabled':{'present_before':True, 'before':True, 'after':False},
                                'consent':{'present_before':True, 'before':True, 'after':False}}
    assert receipt['transformations']['documents_original_path'][0]['document_id'] == 11
    paths = AppPaths.create(target)
    assert json.loads((target / 'instance.json').read_text())['application'] == 'StudyCenter.Public'
    with sqlite3.connect(target / 'core' / 'campusboard.sqlite3') as con:
        assert con.execute('SELECT id,status,pinned,seen FROM items').fetchone() == (17,'done',1,1)
        assert con.execute('SELECT id,item_id,document_id FROM item_evidence').fetchone() == (19,17,11)
        assert con.execute('SELECT job_id FROM notification_reads').fetchone() == ('job',)
        assert con.execute('SELECT state FROM reminder_jobs').fetchone() == ('delivered',)
        name, rev = con.execute('SELECT original_path,revision FROM documents').fetchone()
        assert Path(name).read_bytes() == b'synthetic document original' and rev == 'rev1'
        settings = json.loads(con.execute("SELECT value FROM app_preferences WHERE name='google_calendar'").fetchone()[0])
        assert settings == {'enabled':False,'consent':False,'minute_marker':False}
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    with TestClient(app) as client:
        assert client.get('/overview').status_code == 200
        assert app.state.google.options()['enabled'] is False
        assert app.state.google.options()['consent'] is False
    assert not (target / 'studycenter-public.sqlite3').exists()


def test_failure_retry_collision_and_no_secret_copy(tmp_path):
    source = backup(tmp_path, bad_document=True)
    target = tmp_path / 'new-public'
    before = digest(source.read_bytes())
    with pytest.raises(IceImportError): import_ice(source, target)
    assert not target.exists() and digest(source.read_bytes()) == before
    assert not list(tmp_path.glob('.studycenter-ice-import-*'))
    source = backup(tmp_path, secret=True)
    with pytest.raises(IceImportError): import_ice(source, target)
    assert not target.exists()
    source = backup(tmp_path)
    import_ice(source, target)
    marker = (target / 'instance.json').read_bytes()
    with pytest.raises(IceImportError): import_ice(source, target)
    assert (target / 'instance.json').read_bytes() == marker
    assert not any(b'not-to-copy' in p.read_bytes() for p in target.rglob('*') if p.is_file())


def test_manifest_and_schema_fail_closed(tmp_path):
    source = backup(tmp_path)
    with zipfile.ZipFile(source, 'a') as z:
        z.writestr('../unexpected', b'bad')
    with pytest.raises(IceImportError): import_ice(source, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


def test_quest_and_explicit_cli(tmp_path):
    source = backup(tmp_path, include_quest=True)
    target = tmp_path / 'cli-public'
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'import_ice.py'
    run = subprocess.run([sys.executable, str(script), '--backup', str(source), '--destination', str(target)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    with closing(sqlite3.connect(target / 'core' / 'campusboard.sqlite3')) as con:
        assert con.execute('SELECT id,request_id,state FROM quests').fetchone() == ('q', 'req', 'draft')


def test_rejects_schema_change(tmp_path):
    source = backup(tmp_path)
    with zipfile.ZipFile(source) as z:
        files = {n:z.read(n) for n in z.namelist()}
    db = tmp_path / 'changed.sqlite3'
    db.write_bytes(files['studycenter_ice.sqlite3'])
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('ALTER TABLE items ADD COLUMN unknown TEXT')
    files['studycenter_ice.sqlite3'] = db.read_bytes()
    manifest = json.loads(files['manifest.json'])
    manifest['files']['studycenter_ice.sqlite3'] = {'size':len(files['studycenter_ice.sqlite3']), 'sha256':digest(files['studycenter_ice.sqlite3'])}
    files['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(source, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    with pytest.raises(IceImportError, match='Tabellenspalten'):
        import_ice(source, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


def test_unreferenced_manifested_document_rejected(tmp_path):
    source = backup(tmp_path)
    with zipfile.ZipFile(source) as z:
        files = {n:z.read(n) for n in z.namelist()}
    extra = b'extra original'
    name = 'documents/' + digest(extra) + '.pdf'
    files[name] = extra
    manifest = json.loads(files['manifest.json'])
    manifest['files'][name] = {'size':len(extra), 'sha256':digest(extra)}
    files['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(source, 'w') as z:
        for filename, data in files.items(): z.writestr(filename, data)
    before = digest(source.read_bytes())
    with pytest.raises(IceImportError, match='nicht zugeordnetes'):
        import_ice(source, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()
    assert digest(source.read_bytes()) == before


def test_private_staging_failure_happens_before_backup_extraction(tmp_path, monkeypatch):
    import studycenter_public.ice_migration as migration
    source = backup(tmp_path)
    target = tmp_path / 'target'
    def cannot_protect(path):
        assert not list(path.iterdir())
        raise OSError('synthetic failure')
    monkeypatch.setattr(migration, 'restrict_permissions', cannot_protect)
    with pytest.raises(IceImportError):
        import_ice(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.studycenter-ice-import-*'))


def test_stage_permissions_before_extraction(tmp_path, monkeypatch):
    import os
    import stat
    import studycenter_public.ice_migration as migration
    source = backup(tmp_path)
    original = migration._extract
    checked = []
    def check(archive, destination):
        stage = destination.parent
        if os.name == 'nt':
            # Query the actual protected-DACL flag rather than parsing localized icacls output.
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('advapi32', use_last_error=True)
            api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            api.GetSecurityDescriptorControl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
            needed = wintypes.DWORD()
            api.GetFileSecurityW(str(stage), 4, None, 0, ctypes.byref(needed))
            descriptor = ctypes.create_string_buffer(needed.value)
            assert api.GetFileSecurityW(str(stage), 4, descriptor, needed, ctypes.byref(needed))
            control, revision = wintypes.WORD(), wintypes.DWORD()
            assert api.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
            assert control.value & 0x1000  # SE_DACL_PROTECTED
        else:
            assert stat.S_IMODE(stage.stat().st_mode) & 0o077 == 0
        assert not list(destination.iterdir())
        checked.append(True)
        return original(archive, destination)
    monkeypatch.setattr(migration, '_extract', check)
    import_ice(source, tmp_path / 'target')
    assert checked == [True]
