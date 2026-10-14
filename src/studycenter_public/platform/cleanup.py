"""Fail-closed per-file ownership preflight, followed by explicit own-data removal."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
from uuid import UUID
import zipfile
from contextlib import closing
from .secrets import SecretStoreUnavailable


def _digest(path):
    with path.open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def _no_link(path):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
        raise ValueError('Verknüpfung oder Reparse-Punkt blockiert die Bereinigung; Inhalte bleiben erhalten.')
    return info


def _no_link_chain(path):
    for ancestor in (path,*path.parents):
        if ancestor.exists() or ancestor.is_symlink():_no_link(ancestor)


def _json(path):
    if path.stat().st_size>4*1024**2:raise ValueError('Konfiguration überschreitet die Prüfgrenze.')
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError):raise ValueError('Konfiguration ist nicht als eigene App-Datei überprüfbar.') from None


def _external(kind):
    if sys.platform=='win32':return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'StudyCenter-Public'/kind.capitalize()
    variable='XDG_CONFIG_HOME' if kind=='config' else 'XDG_CACHE_HOME'
    fallback=Path.home()/('.config' if kind=='config' else '.cache')
    return Path(os.environ.get(variable,fallback))/'studycenter-public'


def _originals(database,documents):
    if not database.exists():return {}
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as connection:
            connection.execute('PRAGMA trusted_schema=OFF');connection.execute('PRAGMA query_only=ON')
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if connection.execute('PRAGMA user_version').fetchone()[0]!=1 or not {'documents','pages','items','sources','action_receipts','settings'}<=tables or connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Datenbank ist nicht als intakte eigene App-Datenbank überprüfbar.')
            expected={}
            for filename,digest in connection.execute('SELECT original_path,hash FROM documents'):
                if not filename:continue
                path=Path(filename)
                _no_link_chain(path)
                if not path.is_absolute() or path.resolve()!=path or not path.is_relative_to(documents) or not re.fullmatch('[a-f0-9]{64}',digest or ''):
                    raise ValueError('Originalregistrierung liegt außerhalb des eigenen Dokumentspeichers.')
                if path in expected and expected[path]!=digest:raise ValueError('Widersprüchliche Originalhashes blockieren die Bereinigung.')
                expected[path]=digest
            return expected
    except sqlite3.Error:raise ValueError('Datenbank nicht sicher lesbar; Instanz und Zugangsdaten bleiben erhalten.') from None


def _backup(path):
    if not re.fullmatch(r'studycenter-public-\d{8}-\d{6}-[a-f0-9]{8}\.zip',path.name):return False
    try:
        with zipfile.ZipFile(path) as archive:
            members=archive.infolist();names=[member.filename for member in members]
            if len(names)!=len(set(names)) or len(names)>20000 or sum(member.file_size for member in members)>2*1024**3:return False
            for member in members:
                name=member.filename
                if member.is_dir() or stat.S_ISLNK(member.external_attr>>16) or '\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:return False
                if name not in ('manifest.json','database.sqlite3') and not re.fullmatch(r'originals/[a-f0-9]{64}\.bin',name):return False
            if archive.getinfo('manifest.json').file_size>4*1024**2 or archive.getinfo('database.sqlite3').file_size>128*1024**2:return False
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('format')!='studycenter-public' or manifest.get('version')!=1 or manifest.get('schema_version')!=1 or set(manifest.get('files',{}))!=set(names)-{'manifest.json'}:return False
            for name,record in manifest['files'].items():
                if archive.getinfo(name).file_size!=record['size']:return False
                with archive.open(name) as source:
                    if hashlib.file_digest(source,'sha256').hexdigest()!=record['sha256']:return False
            with closing(sqlite3.connect(':memory:')) as connection:
                connection.deserialize(archive.read('database.sqlite3'))
                connection.execute('PRAGMA trusted_schema=OFF');connection.execute('PRAGMA query_only=ON')
                objects=connection.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if any(kind in ('trigger','view') for _,kind in objects):return False
                if connection.execute('PRAGMA user_version').fetchone()[0]!=1 or connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':return False
                if not {'documents','pages','items','sources','action_receipts'}<={name for name,kind in objects if kind=='table'}:return False
                for original,digest in connection.execute('SELECT original_path,hash FROM documents'):
                    if original and (original not in manifest['files'] or not original.startswith('originals/') or manifest['files'][original]['sha256']!=digest):return False
        return True
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,zipfile.BadZipFile):return False


def _plan(paths,secrets,confirmed):
    if confirmed is not True:raise ValueError('Endgültige Entfernung muss ausdrücklich bestätigt sein.')
    root=Path(paths.data).absolute();_no_link_chain(root);_no_link(root)
    identity_path=root/'instance.json';_no_link(identity_path);identity=_json(identity_path)
    if not isinstance(identity,dict) or identity.get('application')!='StudyCenter.Public' or identity.get('instance_id')!=paths.instance_id or identity.get('host_id')!=paths.host_id:
        raise ValueError('Der Datenordner gehört nicht dieser Public-Instanz.')
    UUID(paths.instance_id)
    for value,name in ((paths.documents,'documents'),(paths.profiles,'browser-profiles'),(paths.logs,'logs')):
        if Path(value).absolute()!=root/name:raise ValueError('App-Datenziel passt nicht zum exakt benannten Instanzverzeichnis.')
    for value,kind in ((paths.config,'config'),(paths.cache,'cache')):
        if Path(value).absolute() not in {root/kind,_external(kind).absolute()}:raise ValueError('Konfigurations- oder Cacheziel passt nicht zum exakt eigenen Verzeichnis.')
    managed={Path(value).absolute() for value in (paths.documents,paths.profiles,paths.logs,paths.cache,paths.config,root/'backups')}
    for directory in managed:
        if directory in (root,Path(paths.program).resolve()):raise ValueError('Ungültiges Bereinigungsziel.')
        if not directory.is_relative_to(root) and directory not in {_external('config').absolute(),_external('cache').absolute()}:
            raise ValueError('Bereinigungsziel liegt außerhalb der exakt eigenen App-Verzeichnisse.')
        _no_link_chain(directory)
        if directory.resolve()!=directory:raise ValueError('Pfadalias oder aufsteigender Pfad blockiert die Bereinigung.')
        if directory.exists():_no_link(directory)
    database=Path(paths.database).absolute()
    if database!=root/'studycenter-public.sqlite3':raise ValueError('Datenbankziel passt nicht zur Instanz.')
    if database.exists():_no_link(database)
    originals=_originals(database,Path(paths.documents).absolute())
    expected={identity_path:None,**originals}
    if database.exists():expected[database]=None
    directories={root,*managed}
    for path in originals:
        parent=path.parent
        while parent!=root and parent.is_relative_to(root):directories.add(parent);parent=parent.parent
    services=['chatgpt/registrations','mcp/access','mail'];config=Path(paths.config).absolute()
    for filename in ('providers.json','settings.json'):
        path=config/filename
        if not path.exists():continue
        _no_link(path);data=_json(path)
        if not isinstance(data,dict):raise ValueError('Konfiguration ist nicht überprüfbar.')
        if filename=='providers.json':
            if set(data)-{'profiles','active'} or not isinstance(data.get('profiles',[]),list):raise ValueError('Providerkonfiguration ist nicht überprüfbar.')
            from ..providers.contracts import ProviderProfile
            profiles=[ProviderProfile(**profile) for profile in data.get('profiles',[])]
            if data.get('active') not in {None,*(profile.profile_id for profile in profiles)}:raise ValueError('Providerauswahl ist nicht überprüfbar.')
            services.extend('provider/'+profile.profile_id for profile in profiles)
        elif set(data)-{'onboarding_step','onboarding_complete','theme','compact','direct_mode','cloud_consent','mcp_enabled','mcp_write','speech_enabled','speech_model','background','autostart'}:
            raise ValueError('Unbekannte Einstellung blockiert die Bereinigung.')
        expected[path]=None
    runtime=root/'runtime.json'
    if runtime.exists():
        _no_link(runtime);data=_json(runtime)
        from urllib.parse import urlsplit
        address=urlsplit(data.get('url',''))
        if set(data)!={'url','pid','instance_id'} or data['instance_id']!=paths.instance_id or type(data['pid']) is not int or data['pid']<1 or address.scheme!='http' or address.hostname!='127.0.0.1' or not address.port or address.username or address.password:
            raise ValueError('Laufzeitdatei ist nicht als eigene App-Datei überprüfbar.')
        expected[runtime]=None
    shutdown=root/'shutdown.request'
    if shutdown.exists():
        _no_link(shutdown)
        if shutdown.read_bytes()!=b'stop':raise ValueError('Unbekannter Inhalt in der Shutdown-Datei.')
        expected[shutdown]=None
    lock=root/'instance.lock'
    if lock.exists():
        _no_link(lock)
        if lock.stat().st_size:raise ValueError('Unbekannter Inhalt in der Instanzsperre.')
        expected[lock]=None
    cache=Path(paths.cache).absolute();directories.update((cache/'ocr',cache/'speech',Path(paths.profiles).absolute()/'tu-browser'))
    speech=cache/'speech'
    if speech.is_dir():
        from ..services.speech import MODELS
        for name,manifest in MODELS.items():
            model=speech/(name+'-'+manifest['revision'])
            if not model.exists():continue
            _no_link(model);manifest_file=model/'manifest.json';_no_link(manifest_file)
            if _json(manifest_file)!=manifest:raise ValueError('Sprachmodellmanifest stimmt nicht mit dieser Version überein.')
            directories.add(model);expected[manifest_file]=None
            for filename,record in manifest['files'].items():expected[model/filename]=record['sha256']
    backups=root/'backups'
    def inspect(directory):
        if not directory.exists():return
        _no_link(directory)
        for path in directory.iterdir():
            info=_no_link(path)
            if stat.S_ISDIR(info.st_mode):
                if path not in directories:raise ValueError('Unbekannter Unterordner blockiert die Bereinigung: '+path.name)
                inspect(path)
            elif stat.S_ISREG(info.st_mode):
                if path not in expected and path.parent==backups and _backup(path):expected[path]=None
                if path not in expected:raise ValueError('Dateibesitz nicht nachweisbar: '+path.name+'. Datei sichern/verschieben; Browser- und Cachedateien ohne Besitznachweis werden nicht gelöscht.')
            else:raise ValueError('Nicht reguläre Datei blockiert die Bereinigung.')
    inspect(root)
    for directory in managed:
        if not directory.is_relative_to(root):inspect(directory)
    hashes={}
    for path,known_hash in expected.items():
        if not path.exists():raise ValueError('Registrierte Datei fehlt; Besitzprüfung bleibt unvollständig.')
        _no_link(path);actual=_digest(path)
        if known_hash is not None and actual!=known_hash:raise ValueError('Registrierte Datei verletzt ihren erwarteten SHA256; nichts wird entfernt.')
        hashes[path]=actual
    if getattr(secrets,'instance_id',None)!=paths.instance_id or not secrets.persistent or getattr(secrets,'session_only',False):
        raise SecretStoreUnavailable('OS-Zugangsspeicher muss vor der Bereinigung verfügbar sein. Sitzungsspeicherung beweist nicht, dass frühere OS-Zugänge entfernt wurden; Instanzidentität bleibt erhalten.')
    for service in services:secrets.get(service)
    return expected,directories,services,hashes,lock


def cleanup_preflight(paths,secrets,*,confirmed=False):
    files,directories,services,hashes,lock=_checked_plan(paths,secrets,confirmed)
    return {'ready':True,'files':len(files),'directories':len(directories),'credential_services':len(services),'unknown_files_preserved':True}


def purge_public_data(paths,secrets,*,confirmed=False):
    files,directories,services,hashes,lock=_checked_plan(paths,secrets,confirmed)
    fresh_files,_,_,fresh_hashes,_=_checked_plan(paths,secrets,confirmed)
    if set(files)!=set(fresh_files) or hashes!=fresh_hashes:raise ValueError('Dateien während der Besitzprüfung verändert; Bereinigung abgebrochen.')
    for service in services:secrets.delete(service)
    identity=Path(paths.data)/'instance.json'
    for path in sorted((path for path in files if path not in (identity,lock)),key=str):
        _no_link_chain(path);_no_link(path)
        if _digest(path)!=hashes[path]:raise ValueError('Datei nach Besitzprüfung verändert; Instanzidentität bleibt erhalten.')
        path.unlink()
    for directory in sorted(directories,key=lambda path:len(path.parts),reverse=True):
        if directory==Path(paths.data) or not directory.exists():continue
        _no_link_chain(directory);_no_link(directory);directory.rmdir()
    _no_link_chain(identity)
    if _digest(identity)!=hashes[identity]:raise ValueError('Instanzidentität nach Besitzprüfung verändert; Datei bleibt erhalten.')
    identity.unlink()
    return {'removed_own_data':True,'removed_own_credentials':True,'unknown_files_preserved':True}


def _checked_plan(paths,secrets,confirmed):
    try:return _plan(paths,secrets,confirmed)
    except (OSError,TypeError,UnicodeError):raise ValueError('Besitzprüfung nicht vollständig lesbar; Daten und Instanzidentität bleiben erhalten.') from None
