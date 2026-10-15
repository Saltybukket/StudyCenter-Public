import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import threading

import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.storage.db import Database
from studycenter_public.services.documents import DocumentService, OCRSettings

spec = importlib.util.spec_from_file_location('own_installer_readiness', Path(__file__).resolve().parents[1] / 'scripts/smoke_installer_vm.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_update_snapshot_includes_first_start_search_index_and_preserves_existing_data(tmp_path):
    paths = AppPaths.create(tmp_path/'Synthetic data')
    db = Database(paths.database)
    item = db.add_item({'kind':'note','title':'Synthetic retained note'})
    before_identity = json.loads((paths.data/'instance.json').read_text())
    before = paths.database.read_bytes()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/api/health'
            requests.append(self.path)
            DocumentService(db, paths, ocr=OCRSettings())
            content = json.dumps({'application':'StudyCenter.Public','instance_id':paths.instance_id}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(content))); self.end_headers()
            self.wfile.write(content)
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    (paths.data/'runtime.json').write_text(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port)}))
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        identity, snapshot = controller.ready_instance_snapshot(type('OwnProcess',(),{'poll':lambda self:None})(), paths.data, timeout=3)
    finally:
        server.shutdown(); thread.join(); server.server_close()
    with sqlite3.connect(':memory:') as con:
        con.deserialize(snapshot)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'document_text_index' in tables
        assert con.execute('SELECT payload FROM items WHERE id=?',(item['id'],)).fetchone()[0] == db.one('SELECT payload FROM items WHERE id=?',(item['id'],))['payload']
    assert identity == before_identity and snapshot != before
    assert snapshot == paths.database.read_bytes() and requests == ['/api/health']


def test_snapshot_refuses_exited_application(tmp_path):
    data=tmp_path/'Synthetic data';data.mkdir()
    (data/'instance.json').write_text(json.dumps({'application':'StudyCenter.Public','instance_id':'synthetic'}))
    (data/'studycenter-public.sqlite3').write_bytes(b'synthetic bytes')
    with pytest.raises(RuntimeError,match='exited before readiness'):
        controller.ready_instance_snapshot(type('ExitedProcess',(),{'poll':lambda self:1})(), data, timeout=.1)
