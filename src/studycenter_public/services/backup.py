"""Checksummed portable backups of database decisions and retained originals."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..storage.db import encode

MAX_BYTES=2*1024**3
SECRET=re.compile(r'password|passwd|secret|token|cookie|credential|authorization|api[_-]?key',re.I)


def digest(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def has_secret(value):
    if isinstance(value,dict):return any(SECRET.search(str(k)) or has_secret(v) for k,v in value.items())
    if isinstance(value,list):return any(has_secret(v) for v in value)
    return isinstance(value,str) and bool(re.search(r'(?:[?&](?:token|password|api_key)=|://[^/]+:[^/]+@)',value,re.I))


class BackupService:
    def __init__(self,db,paths):
        self.db=db;self.paths=paths

    def create(self,output=None):
        directory=Path(self.paths.data)/'backups';directory.mkdir(parents=True,exist_ok=True)
        target=Path(output) if output else directory/f"studycenter-public-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.zip"
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Backup target already exists')
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            stage=Path(temporary);database=stage/'database.sqlite3'
            with closing(sqlite3.connect(self.db.path)) as source,closing(sqlite3.connect(database)) as destination:source.backup(destination)
            files={'database.sqlite3':database}
            with closing(sqlite3.connect(database)) as con:
                con.row_factory=sqlite3.Row
                for row in con.execute('SELECT id,hash,original_path,metadata FROM documents').fetchall():
                    if not row['original_path']:continue
                    original=Path(row['original_path'])
                    if original.is_symlink() or not original.resolve().is_relative_to(Path(self.paths.documents).resolve()) or not original.is_file():raise ValueError('Retained original missing or outside the document store')
                    original_hash=digest(original)
                    if original_hash!=row['hash']:raise ValueError('Retained original differs from its stored source hash')
                    name='originals/'+original_hash+'.bin';files[name]=original
                    metadata=json.loads(row['metadata']);metadata['original_path']=name
                    if 'path' in metadata:metadata['path']=name
                    con.execute('UPDATE documents SET original_path=?,metadata=? WHERE id=?',(name,encode(metadata),row['id']))
                for row in con.execute('SELECT metadata FROM sources'):
                    if has_secret(json.loads(row['metadata'])):raise ValueError('Source configuration contains credentials; backup refused')
                for row in con.execute('SELECT name,value FROM settings'):
                    if SECRET.search(row['name']) or has_secret(json.loads(row['value'])):raise ValueError('Settings contain credentials; backup refused')
                tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                counts={table:con.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
                con.commit()
            if len(files)>19_999 or sum(path.stat().st_size for path in files.values())>MAX_BYTES:raise ValueError('Backup exceeds supported archive bounds')
            manifest={'format':'studycenter-public','version':1,'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'counts':counts,'files':{name:{'size':path.stat().st_size,'sha256':digest(path)} for name,path in files.items()},'excluded':['credentials','browser profiles','cache','logs','machine configuration']}
            partial=target.with_name(target.name+'.'+uuid4().hex+'.partial')
            try:
                with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json',encode(manifest))
                    for name,path in files.items():archive.write(path,name)
                os.replace(partial,target)
            finally:partial.unlink(missing_ok=True)
        return target

    def _inspect(self,archive_path,stage):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members=archive.infolist();names=[member.filename for member in members]
                if len(names)!=len(set(names)) or len(names)>20_000 or sum(m.file_size for m in members)>MAX_BYTES:raise ValueError('Backup archive exceeds supported bounds')
                for member in members:
                    name=member.filename
                    if member.is_dir() or stat.S_ISLNK(member.external_attr>>16) or '\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or not (name in ('manifest.json','database.sqlite3') or re.fullmatch(r'originals/[a-f0-9]{64}\.bin',name)):raise ValueError('Unsupported backup archive member')
                if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>4*1024**2:raise ValueError('Backup manifest missing or oversized')
                manifest=json.loads(archive.read('manifest.json'))
                if manifest.get('format')!='studycenter-public' or manifest.get('version')!=1 or manifest.get('schema_version')!=1 or set(manifest.get('files',{}))!=set(names)-{'manifest.json'} or 'database.sqlite3' not in names:raise ValueError('Unsupported or inconsistent backup manifest')
                for name,info in manifest['files'].items():
                    output=stage/name;output.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(name) as source,output.open('wb') as target:shutil.copyfileobj(source,target)
                    if output.stat().st_size!=info.get('size') or digest(output)!=info.get('sha256'):raise ValueError('Backup checksum mismatch')
            with closing(sqlite3.connect(stage/'database.sqlite3')) as con:
                con.execute('PRAGMA trusted_schema=OFF')
                objects=con.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if any(kind in ('trigger','view') for _,kind,_ in objects):raise ValueError('Executable SQL objects are not allowed in a backup')
                if con.execute('PRAGMA user_version').fetchone()[0]!=1 or con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid backup database')
                tables={name for name,kind,_ in objects if kind=='table'}
                if not {'items','documents','pages','item_evidence','action_receipts','decisions','sources'}<=tables:raise ValueError('Backup database missing required tables')
                if con.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('Backup evidence references are invalid')
                for original,expected,metadata in con.execute('SELECT original_path,hash,metadata FROM documents'):
                    if original and (original not in manifest['files'] or not original.startswith('originals/')):raise ValueError('Original reference outside archive')
                    if original and manifest['files'][original]['sha256']!=expected:raise ValueError('Original does not match its evidence hash')
                    json.loads(metadata)
                for metadata, in con.execute('SELECT metadata FROM sources'):
                    if has_secret(json.loads(metadata)):raise ValueError('Backup contains credentials')
                for name,value in con.execute('SELECT name,value FROM settings'):
                    if SECRET.search(name) or has_secret(json.loads(value)):raise ValueError('Backup contains credentials')
                # Counts derive from validated SQLite, not caller-provided metadata.
                manifest['counts']={table:con.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
            return manifest
        except (zipfile.BadZipFile,sqlite3.Error,KeyError,TypeError,json.JSONDecodeError) as exc:
            raise ValueError('Invalid backup archive') from exc

    def preview(self,archive_path):
        directory=Path(self.paths.data)/'backups';directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            manifest=self._inspect(archive_path,Path(tmp))
        return manifest | {'preview':True,'archive':str(Path(archive_path).resolve()),'replaces':'database and retained originals','credentials_restored':False}

    def restore(self,archive_path,confirmed=False):
        if not confirmed:return self.preview(archive_path)
        with self.db.exclusive():return self._restore(archive_path)

    def _restore(self,archive_path):
        directory=Path(self.paths.data)/'backups';directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            stage=Path(tmp);manifest=self._inspect(archive_path,stage)
            rollback=self.create()
            documents=Path(self.paths.documents);documents.mkdir(parents=True,exist_ok=True)
            created=[]
            try:
                with closing(sqlite3.connect(stage/'database.sqlite3')) as con:
                    for doc_id,original,metadata in con.execute('SELECT id,original_path,metadata FROM documents').fetchall():
                        if not original:continue
                        target=documents/Path(original).name
                        if target.is_symlink():raise ValueError('Unsafe original destination')
                        if target.exists() and digest(target)!=manifest['files'][original]['sha256']:raise ValueError('Conflicting retained original')
                        if not target.exists():
                            shutil.copy2(stage/original,target);created.append(target)
                        meta=json.loads(metadata);meta['original_path']=str(target.resolve())
                        if 'path' in meta:meta['path']=str(target.resolve())
                        con.execute('UPDATE documents SET original_path=?,metadata=? WHERE id=?',(str(target.resolve()),encode(meta),doc_id))
                    con.commit()
                # An open app should invoke this under its process-wide exclusive lock.
                os.replace(stage/'database.sqlite3',self.db.path)
            except BaseException:
                for target in created:target.unlink(missing_ok=True)
                raise
        return {'state':'restored','preview':False,'rollback':str(rollback),'counts':manifest['counts'],'credentials_restored':False}
