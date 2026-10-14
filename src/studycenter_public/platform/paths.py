"""Independent per-user paths; never import parent environment files or profiles."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import uuid

def restrict_permissions(path: Path):
    """Restrict only an explicitly created application directory."""
    path=Path(path).resolve()
    if sys.platform=='win32':
        import subprocess
        username=os.environ.get('USERNAME')
        domain=os.environ.get('USERDOMAIN')
        if not username: raise OSError('Benutzerkennung für Profilrechte nicht verfügbar')
        account=(domain+'\\' if domain else '')+username
        result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'{account}:(OI)(CI)F'],capture_output=True)
        if result.returncode: raise OSError('Private Profilrechte konnten nicht gesetzt werden')
    else: path.chmod(0o700)

def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'win32': path.chmod(0o700)
    return path

def atomic_json(path: Path, data):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    if sys.platform != 'win32': temporary.chmod(0o600)
    temporary.replace(path)

@dataclass(frozen=True)
class AppPaths:
    program: Path
    data: Path
    config: Path
    cache: Path
    logs: Path
    profiles: Path
    documents: Path
    database: Path
    instance_id: str
    host_id: str

    @classmethod
    def create(cls, data_dir=None):
        program=Path(__file__).resolve().parents[1]
        if sys.platform=='win32':
            root=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'StudyCenter-Public'
            default_data=root/'Data'; default_config=root/'Config'; default_cache=root/'Cache'
        else:
            default_data=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'studycenter-public'
            default_config=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'studycenter-public'
            default_cache=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'studycenter-public'
        chosen=data_dir or os.environ.get('STUDYCENTER_PUBLIC_DATA_DIR')
        data=Path(chosen or default_data).expanduser().resolve()
        if data==program or program.is_relative_to(data) or data.is_relative_to(program):
            raise ValueError('Nutzerdaten brauchen einen eigenen Ordner außerhalb der Programmdateien.')
        identity_path=data/'instance.json'
        if data.exists() and any(data.iterdir()) and not identity_path.is_file():
            raise ValueError('Dieser nicht leere Ordner gehört noch keiner Public-Instanz. Einen neuen leeren Datenordner wählen; vorhandene Dateien bleiben unberührt.')
        if identity_path.exists():
            identity=json.loads(identity_path.read_text(encoding='utf-8'))
            if identity.get('application')!='StudyCenter.Public' or set(identity)-{'application','instance_id','host_id'}:
                raise ValueError('Der gewählte Ordner ist keine Public-Instanz.')
            uuid.UUID(identity['instance_id']); uuid.UUID(identity['host_id'].removeprefix('urn:uuid:'))
        else:
            identity={'application':'StudyCenter.Public','instance_id':str(uuid.uuid4()),'host_id':'urn:uuid:'+str(uuid.uuid4())}
            private_directory(data)
            atomic_json(identity_path,identity)
        config=private_directory(data/'config' if chosen else default_config)
        cache=private_directory(data/'cache' if chosen else default_cache)
        return cls(program,data,config,cache,private_directory(data/'logs'),
                   private_directory(data/'browser-profiles'),private_directory(data/'documents'),
                   data/'studycenter-public.sqlite3',identity['instance_id'],identity['host_id'])
