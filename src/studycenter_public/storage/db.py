"""SQLite persistence: immutable originals and evidence, projected personal state."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..domain.evidence import effective_evidence, newer_observation, newer_publication
from ..domain.validation import validate_values


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE sources(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE TABLE courses(id INTEGER PRIMARY KEY,title TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE TABLE documents(id INTEGER PRIMARY KEY,source_id INTEGER REFERENCES sources(id),url TEXT NOT NULL,title TEXT NOT NULL,hash TEXT NOT NULL,revision INTEGER NOT NULL,fetched_at TEXT NOT NULL,published_at TEXT,mime TEXT NOT NULL,original_path TEXT,metadata TEXT NOT NULL);
CREATE TABLE pages(document_id INTEGER REFERENCES documents(id),page INTEGER NOT NULL,text TEXT NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(document_id,page));
CREATE TABLE items(id INTEGER PRIMARY KEY,identity TEXT UNIQUE NOT NULL,source_id INTEGER REFERENCES sources(id),course_id INTEGER,payload TEXT NOT NULL,overrides TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'open',version INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE item_evidence(id INTEGER PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id),document_id INTEGER NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,anchor TEXT NOT NULL,quote TEXT NOT NULL,payload TEXT NOT NULL,current INTEGER NOT NULL DEFAULT 1,UNIQUE(item_id,document_id,page,anchor,quote));
CREATE TABLE decisions(id INTEGER PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id),field TEXT NOT NULL,value TEXT NOT NULL,document_id INTEGER REFERENCES documents(id),document_hash TEXT,reason TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(item_id,field));
CREATE TABLE action_receipts(request_id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,receipt TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE settings(name TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE reminder_jobs(job_key TEXT PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id),due_at TEXT NOT NULL,state TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX evidence_item ON item_evidence(item_id,current);
CREATE INDEX document_source ON documents(source_id,url,fetched_at);
"""

_LOCKS={}
_LOCKS_GUARD=threading.Lock()


class Database:
    def __init__(self, path):
        self.path = Path(path)
        with _LOCKS_GUARD:
            self._lock=_LOCKS.setdefault(str(self.path.resolve()),threading.RLock())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('Database schema is newer than this application')
            if version == 0:
                # executescript commits implicitly; wrap migration itself atomically.
                con.executescript('BEGIN IMMEDIATE;'+SCHEMA+'PRAGMA user_version=1;COMMIT;')

    @contextmanager
    def connection(self):
        with self._lock:
            with self._connection() as con:yield con

    @contextmanager
    def exclusive(self):
        """Serialize restore with every database operation in this process."""
        with self._lock:yield

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def rows(self, sql, args=(), connection=None):
        if connection is not None:
            return [dict(r) for r in connection.execute(sql, args)]
        with self.connection() as con:
            return self.rows(sql, args, con)

    def one(self, sql, args=(), connection=None):
        rows = self.rows(sql, args, connection)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connection() as con:
            return con.execute(sql, args).lastrowid

    def upsert_source(self, source_id=None, name='', kind='', metadata=None, **fields):
        name = name or fields.pop('title', '') or fields.get('url', '') or 'Source'
        with self.connection() as con:
            old = self.one('SELECT * FROM sources WHERE id=?', (source_id,), con)
            meta = (json.loads(old['metadata']) if old else {}) | (metadata or {}) | fields
            if old:
                con.execute('UPDATE sources SET name=?,kind=?,metadata=? WHERE id=?', (name, kind or old['kind'], encode(meta), source_id))
            else:
                source_id = con.execute('INSERT INTO sources(id,name,kind,metadata) VALUES(?,?,?,?)',(source_id,name,kind,encode(meta))).lastrowid
        return self.source(source_id)

    def sources(self):
        return [self._source(r) for r in self.rows('SELECT * FROM sources ORDER BY id')]

    def _source(self, row):
        return json.loads(row['metadata']) | row | {'metadata':json.loads(row['metadata']), 'title':row['name']}

    def source(self, source_id):
        row = self.one('SELECT * FROM sources WHERE id=?',(source_id,))
        return self._source(row) if row else None

    def upsert_course(self, source_id, external_id, title, metadata=None):
        """Map a collector's explicit source-local course to an installation ID."""
        if type(source_id) is not int or not self.source(source_id):
            raise ValueError('Course requires an existing source')
        if not isinstance(external_id, str) or not external_id or len(external_id) > 200:
            raise ValueError('Bounded collector course identity required')
        if not isinstance(title, str) or not title.strip() or len(title) > 240:
            raise ValueError('Bounded course title required')
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            existing = self.one("SELECT * FROM courses WHERE json_extract(metadata,'$.source_id')=? AND json_extract(metadata,'$.external_id')=?", (source_id, external_id), con)
            meta = (json.loads(existing['metadata']) if existing else {}) | (metadata or {}) | {'source_id':source_id, 'external_id':external_id}
            if existing:
                course_id = existing['id']
                con.execute('UPDATE courses SET title=?,metadata=? WHERE id=?', (title.strip(), encode(meta), course_id))
            else:
                course_id = con.execute('INSERT INTO courses(title,metadata) VALUES(?,?)', (title.strip(), encode(meta))).lastrowid
            return self._course(self.one('SELECT * FROM courses WHERE id=?', (course_id,), con))

    @staticmethod
    def _course(row):
        return json.loads(row['metadata']) | row | {'metadata':json.loads(row['metadata'])}

    def course(self, course_id):
        row = self.one('SELECT * FROM courses WHERE id=?', (course_id,))
        return self._course(row) if row else None

    def courses(self, source_id=None):
        query = 'SELECT * FROM courses'
        args = ()
        if source_id is not None:
            query += " WHERE json_extract(metadata,'$.source_id')=?"
            args = (source_id,)
        return [self._course(row) for row in self.rows(query + ' ORDER BY id', args)]

    def save_document(self, document, pages=None):
        doc = dict(document)
        pages = pages if pages is not None else doc.pop('pages', [])
        url = doc.get('url',doc.get('source_url',''))
        original=doc.get('original_path',doc.get('path'))
        if doc.get('hash'):digest=doc['hash']
        elif original:
            with Path(original).open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
        else:digest=hashlib.sha256(encode(pages).encode()).hexdigest()
        with self.connection() as con:
            trusted_course_id = doc.get('trusted_course_id')
            if trusted_course_id is not None:
                course = self.one('SELECT * FROM courses WHERE id=?', (trusted_course_id,), con)
                if type(trusted_course_id) is not int or not course or json.loads(course['metadata']).get('source_id') != doc.get('source_id'):
                    raise ValueError('Document course must belong to its retained source')
                doc['course_title'] = course['title']
            # Shared URLs can occur in two explicitly selected courses. Their
            # reading/version families retain their independent course binding.
            existing = self.one("SELECT * FROM documents WHERE source_id IS ? AND url=? AND json_extract(metadata,'$.trusted_course_id') IS ? ORDER BY id DESC LIMIT 1", (doc.get('source_id'),url,trusted_course_id),con)
            if existing and existing['hash'] == digest and not doc.get('force_revision'):
                return self._document(existing,con)
            version = con.execute("SELECT COALESCE(MAX(revision),0)+1 FROM documents WHERE source_id IS ? AND url=? AND json_extract(metadata,'$.trusted_course_id') IS ?",(doc.get('source_id'),url,trusted_course_id)).fetchone()[0]
            doc_id = con.execute('INSERT INTO documents(source_id,url,title,hash,revision,fetched_at,published_at,mime,original_path,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)', (doc.get('source_id'),url,doc.get('title','Document'),digest,version,doc.get('fetched_at') or now(),doc.get('published_at'),doc.get('mime','text/plain'),doc.get('original_path',doc.get('path')),encode(doc))).lastrowid
            for index, page in enumerate(pages,1):
                con.execute('INSERT INTO pages VALUES(?,?,?,?)',(doc_id,page.get('page',index),page['text'],encode(page.get('metadata',{}))))
            return self._document(self.one('SELECT * FROM documents WHERE id=?',(doc_id,),con),con)

    def _document(self,row,con=None):
        return json.loads(row['metadata']) | row | {'metadata':json.loads(row['metadata']),'version':row['revision'],'pages':self.pages(row['id'],con)}

    def documents(self, source_id=None):
        sql = 'SELECT * FROM documents'+(' WHERE source_id=?' if source_id is not None else '')+' ORDER BY id'
        return [self._document(r) for r in self.rows(sql,(source_id,) if source_id is not None else ())]

    def document(self, document_id):
        row = self.one('SELECT * FROM documents WHERE id=?',(document_id,))
        return self._document(row) if row else None

    def pages(self, document_id, connection=None):
        return [r | {'metadata':json.loads(r['metadata'])} for r in self.rows('SELECT * FROM pages WHERE document_id=? ORDER BY page',(document_id,),connection)]

    def add_item(self, value):
        from uuid import uuid4
        payload = dict(value)
        with self.connection() as con:
            item_id = self._create_item(con,payload,'personal:'+str(uuid4()))
        return self.item(item_id)

    def _create_item(self, con, payload, identity, source_id=None, course_id=None):
        validate_values(payload)
        title = payload.get('title','').strip()
        if not title or len(title)>240 or payload.get('kind') not in ('note','task','event','exam','registration','notice'):
            raise ValueError('A bounded title and valid item kind are required')
        return con.execute('INSERT INTO items(identity,source_id,course_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)',(identity,source_id,course_id,encode(payload),now(),now())).lastrowid

    def ingest(self, document, candidate, connection=None):
        if connection is None:
            with self.connection() as con:
                con.execute('BEGIN IMMEDIATE')
                return self.ingest(document,candidate,connection=con)
        doc_id = document if isinstance(document,int) else document['id']
        value = candidate.model_dump(mode='json') if hasattr(candidate,'model_dump') else dict(candidate)
        validate_values(value)
        if value.get('kind')=='note' or not value.get('quote') or not value.get('page') or not value.get('anchor'):raise ValueError('Source candidate requires kind, quote, page and anchor')
        with _existing_connection(connection) as con:
            doc = self.one('SELECT * FROM documents WHERE id=?',(doc_id,),con)
            page = self.one('SELECT text FROM pages WHERE document_id=? AND page=?',(doc_id,value.get('page')),con)
            if not doc or not page or not value.get('quote') or value['quote'] not in page['text']:
                raise ValueError('Evidence quote must occur in the retained source page')
            metadata = json.loads(doc['metadata'])
            trusted_course_id = metadata.get('trusted_course_id')
            course = self.one('SELECT * FROM courses WHERE id=?', (trusted_course_id,), con) if type(trusted_course_id) is int else None
            if not course or json.loads(course['metadata']).get('source_id') != doc['source_id']:
                trusted_course_id = None
            # A model's course_id never establishes source authority. The stored
            # collector binding is checked again at admission, including restores.
            value.pop('course_id', None)
            if trusted_course_id is not None:
                value['course_id'] = trusted_course_id
            scope = (['course', doc['source_id'], trusted_course_id]
                     if value.get('identity_scope') == 'course' and trusted_course_id is not None
                     else ['document', doc['source_id'], trusted_course_id, doc['url'] or str(doc_id)])
            key = value.get('identity_key') or value.get('anchor') or value['title']
            identity = hashlib.sha256(encode([scope,key,value.get('kind')]).encode()).hexdigest()
            existing = self.one('SELECT * FROM items WHERE identity=?',(identity,),con)
            item_id = existing['id'] if existing else self._create_item(con,value,identity,doc['source_id'],trusted_course_id)
            latest = self.one('SELECT d.* FROM documents d JOIN item_evidence e ON e.document_id=d.id WHERE e.item_id=? AND e.current=1 AND d.url=? ORDER BY d.fetched_at DESC,d.id DESC LIMIT 1',(item_id,doc['url']),con)
            current = not latest or latest['id']==doc_id or newer_publication(doc['published_at'],latest['published_at']) or newer_observation(doc['fetched_at'],latest['fetched_at'])
            if latest and newer_publication(latest['published_at'],doc['published_at']):current=False
            if current:
                con.execute('UPDATE item_evidence SET current=0 WHERE item_id=? AND document_id IN(SELECT id FROM documents WHERE source_id IS ? AND url=? AND id!=?)',(item_id,doc['source_id'],doc['url'],doc_id))
            cursor=con.execute('INSERT OR IGNORE INTO item_evidence(item_id,document_id,page,anchor,quote,payload,current) VALUES(?,?,?,?,?,?,?)',(item_id,doc_id,value['page'],value.get('anchor',''),value['quote'],encode(value),int(current)))
            if existing and cursor.rowcount:
                con.execute('UPDATE items SET version=version+1,updated_at=? WHERE id=?',(now(),item_id))
            return self._item(item_id,con)

    def ingest_many(self,document,candidates):
        if not isinstance(candidates,list) or len(candidates)>100:raise ValueError('Extraction batch must be bounded to 100 candidates')
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            return [self.ingest(document,candidate,connection=con) for candidate in candidates]

    def _item(self,item_id,con):
        row=self.one('SELECT * FROM items WHERE id=? AND deleted=0',(item_id,),con)
        if not row:return None
        evidence=self.rows('SELECT e.*,d.url,d.title,d.hash,d.source_id,d.published_at,d.fetched_at,d.mime,s.kind FROM item_evidence e JOIN documents d ON d.id=e.document_id LEFT JOIN sources s ON s.id=d.source_id WHERE e.item_id=? ORDER BY e.id',(item_id,),con)
        source=json.loads(row['payload']);current=[p for p in evidence if p['current']]
        effective,conflict,date_conflict,resolution=effective_evidence(current,source) if current else (source,False,False,{})
        overrides=json.loads(row['overrides']);data=effective | overrides
        decisions=self.rows('SELECT * FROM decisions WHERE item_id=? ORDER BY id',(item_id,),con)
        for decision in decisions:
            decision['value']=json.loads(decision['value'])
            decision['stale']=bool(decision['document_hash'] and not any(p['hash']==decision['document_hash'] for p in current))
            data[decision['field']]=decision['value']
        from ..domain.personal import booking_projection
        booking=booking_projection(data,current)
        result=row | data | {'data':data,'source_payload':effective,'overrides':overrides,'evidence':evidence,'source_claims':[json.loads(p['payload']) for p in current],'conflict':conflict,'date_conflict':date_conflict,'evidence_resolution':resolution,'personal_decisions':decisions,'booked_event':bool(booking),'booking_proof':booking,'personal_event':bool(booking) or row['status']=='planned' or row['identity'].startswith('personal:')}
        return result

    def item(self,item_id):
        with self.connection() as con:return self._item(item_id,con)

    def items(self, query=None, kind=None, active_only=False):
        with self.connection() as con:
            items=[self._item(r['id'],con) for r in self.rows('SELECT id FROM items WHERE deleted=0 ORDER BY id',connection=con)]
        if kind:items=[i for i in items if i['data'].get('kind')==kind]
        if active_only:items=[i for i in items if i['status'] not in ('done','ignored')]
        if query:
            needle=query.casefold();items=[i for i in items if needle in encode(i['data']).casefold() or any(needle in p['quote'].casefold() for p in i['evidence'])]
        return items

    def setting(self,name,default=None):
        row=self.one('SELECT value FROM settings WHERE name=?',(name,))
        return json.loads(row['value']) if row else default

    def set_setting(self,name,value):
        self.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',(name,encode(value)))


@contextmanager
def _existing_connection(connection):
    yield connection
