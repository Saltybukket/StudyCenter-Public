"""Fresh MSYS2 portable OCR archives: checksummed extraction, no installer execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import urllib.request

REPOSITORY='https://repo.msys2.org/mingw/ucrt64/'
TESSERACT='mingw-w64-ucrt-x86_64-tesseract-ocr'
TESSERACT_VERSION='5.5.3-1'
TESSERACT_SHA='7e797396b34e2f0c78c9b9ff4b90d66c068c053d60bded81fc5232892b2b1caf'
LANGUAGES={'eng':'7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2','deu':'19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d'}


def sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download(url,target,expected=None,max_bytes=150_000_000):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and (expected is None or sha256(target)==expected):return target
    # The caller supplies only HTTPS origins published by upstream distributions.
    if not url.startswith(('https://repo.msys2.org/','https://raw.githubusercontent.com/tesseract-ocr/')):raise ValueError('Unsupported component download origin')
    temporary=target.with_suffix(target.suffix+'.partial')
    with urllib.request.urlopen(url,timeout=60) as source,temporary.open('wb') as destination:
        size=0
        while chunk:=source.read(1024*1024):
            size+=len(chunk)
            if size>max_bytes:raise ValueError('Component archive exceeds download bound')
            destination.write(chunk)
    if expected and sha256(temporary)!=expected:raise ValueError('Component archive checksum mismatch')
    temporary.replace(target);return target


def read_database(path):
    data=Path(path).read_bytes()
    if data[:4]==b'\x28\xb5\x2f\xfd':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:data=reader.read(32*1024**2+1)
        if len(data)>32*1024**2:raise ValueError('Package metadata exceeds supported bound')
    records={}
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
        for member in archive:
            if not member.isfile() or PurePosixPath(member.name).name!='desc':continue
            content=archive.extractfile(member).read().decode('utf-8')
            fields={}
            for block in re.split(r'\n\s*\n',content.strip()):
                lines=block.splitlines()
                if lines and lines[0].startswith('%'):fields[lines[0].strip('%')]=lines[1:]
            name=fields['NAME'][0]
            records[name]={'name':name,'base':fields.get('BASE',[name])[0],'version':fields['VERSION'][0],'filename':fields['FILENAME'][0],'sha256':fields['SHA256SUM'][0],'size':int(fields['CSIZE'][0]),'depends':fields.get('DEPENDS',[]),'provides':fields.get('PROVIDES',[]),'licenses':fields.get('LICENSE',[]),'upstream':fields.get('URL',[''])[0]}
    return records


def extract_runtime(archive_path,destination):
    import zstandard
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with Path(archive_path).open('rb') as compressed,zstandard.ZstdDecompressor().stream_reader(compressed) as decoded,tarfile.open(fileobj=decoded,mode='r|') as archive:
        for member in archive:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:raise ValueError('Unsafe component archive path')
            if member.islnk() or member.issym():continue
            if not member.isfile():continue
            binary=(len(name.parts)==3 and name.parts[:2]==('ucrt64','bin') and (name.suffix.casefold()=='.dll' or name.name=='tesseract.exe'))
            notice=(name.parts[:3]==('ucrt64','share','licenses'))
            if not binary and not notice:continue
            if member.size>150_000_000:raise ValueError('Oversized component archive member')
            target=destination/name.name if binary else destination/'licenses'/Path(*name.parts[3:])
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as source,target.open('wb') as output:shutil.copyfileobj(source,output)


def stage_runtime(output,database=None):
    output=Path(output).resolve();downloads=output/'downloads';downloads.mkdir(parents=True,exist_ok=True)
    database=Path(database) if database else download(REPOSITORY+'ucrt64.db',downloads/'ucrt64.db',max_bytes=32*1024**2)
    records=read_database(database);root=records[TESSERACT]
    if root['version']!=TESSERACT_VERSION or root['sha256']!=TESSERACT_SHA:raise ValueError('Pinned OCR archive changed; review new provenance before rebuilding')
    aliases={re.split(r'[<>=]',alias)[0]:name for name,record in records.items() for alias in record['provides']}
    queue=[TESSERACT];selected={}
    while queue:
        name=queue.pop();name=aliases.get(name,name)
        if name in selected:continue
        if name not in records:raise ValueError('Unknown portable runtime dependency: '+name)
        record=records[name];selected[name]=record
        queue.extend(re.split(r'[<>=]',dependency)[0] for dependency in record['depends'])
    if len(selected)>100:raise ValueError('OCR dependency graph exceeds expected bound')
    runtime=output/'runtime';runtime.mkdir(exist_ok=True)
    for name,record in sorted(selected.items()):
        url=REPOSITORY+record['filename'];archive=download(url,downloads/record['filename'],record['sha256'],max_bytes=max(record['size']+1024,100_000))
        if archive.stat().st_size!=record['size']:raise ValueError('OCR archive size mismatch')
        extract_runtime(archive,runtime)
        record['origin']=url;record['source_recipe']='https://github.com/msys2/MINGW-packages/tree/master/'+record['base']
        record['published_package_page']='https://packages.msys2.org/packages/'+name
    trained=runtime/'tessdata';trained.mkdir(exist_ok=True)
    for language,digest in LANGUAGES.items():
        download('https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/'+language+'.traineddata',trained/(language+'.traineddata'),digest,max_bytes=10*1024**2)
    manifest={'component':'Tesseract portable MSYS2 ucrt64','version':TESSERACT_VERSION,'metadata_sha256':sha256(database),'published_package_page':'https://packages.msys2.org/packages/'+TESSERACT,'packages':list(selected.values()),'languages':{language:{'sha256':digest,'origin':'https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0','license':'Apache-2.0'} for language,digest in LANGUAGES.items()},'installation':'Archive extraction only; no setup, pacman, registry or global configuration'}
    (runtime/'COMPONENT-MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    print(runtime);return runtime


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('build/windows-ocr'));parser.add_argument('--database',type=Path)
    args=parser.parse_args();stage_runtime(args.output,args.database)
