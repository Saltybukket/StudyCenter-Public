"""Omit Microsoft system runtimes and bind licensed consumers to system names."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import zipfile

ALIASES={'msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll':'msvcp140.dll'}
SYSTEM_NAMES={'msvcp140.dll','msvcp140_1.dll','vcruntime140.dll','vcruntime140_1.dll','concrt140.dll','ucrtbase.dll','d3dcompiler_47.dll'}
NUMPY_PRIMARY={'filename':'numpy-2.5.4-cp312-cp312-win_amd64.whl',
    'url':'https://files.pythonhosted.org/packages/12/33/fefe62073dc8acfd0f2b9ed7c003af2f50aa61555e113e6db02b8f79f145/numpy-2.5.4-cp312-cp312-win_amd64.whl',
    'sha256':'a7b1b6353e36a7e50de2973a38d705c88ee93adcf120673cee7f45a4a3fa223a'}


def digest(data):return hashlib.sha256(data).hexdigest()


def omitted_system_binary(name):
    name=name.casefold()
    return name in SYSTEM_NAMES or name in ALIASES or bool(re.fullmatch(r'(?:(?:api|ext)-ms-win-.*|msvcp\d+(?:_[a-z0-9]+)*(?:-[a-f0-9]+)?|msvcr\d+|vcruntime\d+(?:_[a-z0-9]+)*|concrt\d+)\.dll',name))


def pe_files(bundle):
    for path in sorted(Path(bundle).rglob('*')):
        if path.is_file() and path.suffix.casefold() in {'.exe','.dll','.pyd'}:
            with path.open('rb') as file:
                if file.read(2)==b'MZ':yield path


def import_descriptors(image):
    return list(getattr(image,'DIRECTORY_ENTRY_IMPORT',[]))+list(getattr(image,'DIRECTORY_ENTRY_DELAY_IMPORT',[]))


def rewrite_aliases(path, primary_members):
    """A shorter ASCII DLL name replaces only its existing import-name bytes."""
    import pefile
    path=Path(path);original=path.read_bytes();changed=bytearray(original);edits=[]
    with pefile.PE(data=original) as image:
        for entry in import_descriptors(image):
            name=entry.dll.decode('ascii');canonical=ALIASES.get(name.casefold())
            if not canonical:
                if re.fullmatch(r'(?:msvcp|vcruntime|concrt)\d+.*-[a-f0-9]+\.dll',name,re.I):
                    raise ValueError('Unreviewed mangled system runtime import: '+name)
                continue
            if digest(original) not in primary_members:raise ValueError('Aliased consumer is not an exact verified primary wheel member')
            security=image.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_SECURITY']]
            if security.Size:raise ValueError('Refusing to invalidate a signed consumer')
            rva=entry.struct.Name if hasattr(entry.struct,'Name') else entry.struct.szName
            offset=image.get_offset_from_rva(rva)
            if original[offset:offset+len(entry.dll)]!=entry.dll:raise ValueError('PE import-name offset is inconsistent')
            replacement=canonical.encode()+b'\0'*(len(entry.dll)-len(canonical))
            changed[offset:offset+len(entry.dll)]=replacement
            edits.append({'offset':offset,'from':name,'to':canonical})
    if edits:
        path.write_bytes(changed)
        with pefile.PE(str(path)) as image:
            if any(entry.dll.decode().casefold() in ALIASES for entry in import_descriptors(image)):raise ValueError('Mangled runtime import remains')
        return {'original_sha256':digest(original),'patched_sha256':digest(changed),'primary_member':primary_members[digest(original)],'edits':edits}
    return None


def primary_numpy_members(downloads, version='2.5.4'):
    if version!='2.5.4':raise ValueError('NumPy primary wheel version requires a new reviewed recipe')
    downloads=Path(downloads);downloads.mkdir(parents=True,exist_ok=True)
    record=NUMPY_PRIMARY
    archive=downloads/record['filename']
    if not archive.exists():urllib.request.urlretrieve(record['url'],archive)
    if digest(archive.read_bytes())!=record['sha256']:raise ValueError('Primary NumPy wheel checksum mismatch')
    members={}
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if name.endswith('.pyd'):members[digest(zipped.read(name))]=name
    return members,{'origin':record['url'],'published_metadata':'https://pypi.org/pypi/numpy/2.5.4/json','archive':record['filename'],'archive_sha256':record['sha256'],'license':'NumPy BSD-3-Clause; Microsoft runtime DLLs omitted'}


def prepare(bundle, downloads):
    import pefile
    bundle=Path(bundle).resolve();members,primary=primary_numpy_members(downloads)
    recipe={'policy':'System prerequisite; no Microsoft CRT/UCRT/API-set/D3DCompiler payload or local aliases','primary_inputs':[primary],'patched_consumers':{},'omitted_files':{}}
    recipe['ucrt_api_set_resolution']={'prefix':'api-ms-win-crt-','implementation':'ucrtbase.dll','origin':'https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment'}
    for path in list(pe_files(bundle)):
        if not path.resolve().is_relative_to(bundle):raise ValueError('Native file escapes bundle')
        relative=path.relative_to(bundle).as_posix()
        if omitted_system_binary(path.name):
            with pefile.PE(str(path),fast_load=True) as image:machine=image.FILE_HEADER.Machine
            recipe['omitted_files'][relative]={'sha256':digest(path.read_bytes()),'machine':machine};path.unlink()
        else:
            patch=rewrite_aliases(path,members)
            if patch:recipe['patched_consumers'][relative]=patch
    requirements={}
    for path in pe_files(bundle):
        with pefile.PE(str(path)) as image:
            for entry in import_descriptors(image):
                name=entry.dll.decode().casefold()
                ucrt_api=name.startswith('api-ms-win-crt-')
                if ucrt_api:name='ucrtbase.dll'
                if omitted_system_binary(name) and not name.startswith(('api-ms-win-','ext-ms-win-')):
                    symbols=requirements.setdefault(name,set())
                    for symbol in entry.imports:
                        if ucrt_api and not symbol.name:raise ValueError('Ordinal UCRT API-set import requires a reviewed forwarder mapping')
                        symbols.add(symbol.name.decode('ascii') if symbol.name else '#'+str(symbol.ordinal))
    # ANGLE loads the Windows DirectX compiler dynamically on its D3D path.
    requirements.setdefault('d3dcompiler_47.dll',set()).update({'D3DCompile','D3DReflect'})
    recipe['system_requirements']={name:sorted(symbols) for name,symbols in sorted(requirements.items())}
    (bundle/'SYSTEM-RUNTIME-RECIPE.json').write_text(json.dumps(recipe,sort_keys=True,indent=2),encoding='utf-8')
    lines=[name+'|'+symbol for name,symbols in recipe['system_requirements'].items() for symbol in symbols]
    literal=' + #10 +\n  '.join("'"+line.replace("'","''")+"'" for line in lines) or "''"
    (bundle/'SYSTEM-RUNTIME-REQUIREMENTS.iss').write_text('const SystemRuntimeRequirements =\n  '+literal+';\n',encoding='utf-8')
    if any(omitted_system_binary(path.name) for path in bundle.rglob('*') if path.is_file()):raise ValueError('System runtime payload remains')
    return recipe


def check_system(requirements, directory):
    """Read PE headers/exports; never load, copy, install or repair system DLLs."""
    import pefile
    for name,symbols in requirements.items():
        if Path(name).name!=name or '/' in name or '\\' in name or ':' in name or '..' in name:raise ValueError('Invalid system runtime filename')
        path=Path(directory)/name
        if not path.is_file():raise RuntimeError('Required Windows x64 system runtime is missing: '+name)
        with pefile.PE(str(path)) as image:
            if image.FILE_HEADER.Machine!=0x8664:raise RuntimeError('System runtime is not x64: '+name)
            exports=getattr(image,'DIRECTORY_ENTRY_EXPORT',None)
            actual=set()
            if exports:
                for symbol in exports.symbols:
                    if symbol.address:
                        actual.add('#'+str(symbol.ordinal))
                        if symbol.name:actual.add(symbol.name.decode('ascii'))
            if not set(symbols)<=actual:raise RuntimeError('Installed system runtime lacks required exports: '+name)
    return True
