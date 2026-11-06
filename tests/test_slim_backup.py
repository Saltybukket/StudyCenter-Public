"""The pre-update backup must contain the live WAL and document evidence."""
import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest
import portalocker

from studycenter_public.core.backup import inspect_archive
from studycenter_public.core.db import Database


def load_helper():
    path = Path(__file__).resolve().parents[1]/'packaging/backup_core.py'
    spec = importlib.util.spec_from_file_location('synthetic_backup_helper', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def test_core_backup_snapshots_wal_and_checks_originals(tmp_path):
    data = tmp_path/'data'
    core = data/'core'
    documents = core/'documents'
    documents.mkdir(parents=True)
    database = core/'campusboard.sqlite3'
    Database(database)
    content = b'synthetic original evidence'
    digest = hashlib.sha256(content).hexdigest()
    original = documents/(digest+'.txt')
    original.write_bytes(content)
    with sqlite3.connect(database) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute("INSERT INTO sources(id,kind,title,url) VALUES(1,'manual','synthetic','import:fixture')")
        live.execute("INSERT INTO documents(id,source_id,url,title,mime,hash,fetched_at,original_path) VALUES(1,1,'import:fixture','synthetic','text/plain',?,'2026-01-01',?)", (digest, str(original)))
        live.commit()
        assert database.with_name(database.name+'-wal').exists()
        archive = load_helper().backup(data)
        restored = tmp_path/'restored'
        manifest = inspect_archive(archive, restored)
        assert manifest['counts']['documents'] == 1
        assert (restored/'documents'/(digest+'.txt')).read_bytes() == content
        with sqlite3.connect(restored/'campusboard.sqlite3') as con:
            assert con.execute('SELECT hash,original_path FROM documents').fetchone() == (digest, 'documents/'+digest+'.txt')
        original.write_bytes(b'corrupt')
        with pytest.raises(ValueError, match='stimmt nicht'):
            load_helper().backup(data)
        assert archive.exists()


def test_core_backup_refuses_a_running_instance(tmp_path):
    data = tmp_path/'data'
    core = data/'core'
    core.mkdir(parents=True)
    Database(core/'campusboard.sqlite3')
    with portalocker.Lock(str(data/'instance.lock'), timeout=0):
        with pytest.raises(portalocker.exceptions.LockException):
            load_helper().backup(data)
    assert not (core/'backups').exists()
