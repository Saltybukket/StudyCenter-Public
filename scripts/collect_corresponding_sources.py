"""Fetch and inventory source archives only. Never execute their build recipes."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import ssl
import tarfile
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit


class SourceCollector:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.records=[];self.errors=[];self.historical_attempts=[]

    def fetch(self,url,name,expected=None):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.username or parts.password:raise ValueError('Sources require credential-free HTTPS')
        path=self.root/name
        relative=PurePosixPath(str(name).replace('\\','/'))
        if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):raise ValueError('Source target outside owned directory')
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.resolve().is_relative_to(self.root):raise ValueError('Source target outside owned directory: '+str(relative))
        if not path.exists():
            request=urllib.request.Request(url,headers={'User-Agent':'StudyCenter-Public-source-review/1.0'})
            import certifi
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
            temporary=path.with_suffix(path.suffix+'.partial')
            try:
                with opener.open(request,timeout=60) as response,temporary.open('wb') as target:
                    total=0;deadline=time.monotonic()+300
                    while block:=response.read1(64*1024):
                        if time.monotonic()>deadline:raise TimeoutError('Source retrieval exceeded bounded duration')
                        total+=len(block)
                        if total>512*1024**2:raise ValueError('Source archive exceeds bounded quota')
                        target.write(block)
                temporary.replace(path)
            finally:temporary.unlink(missing_ok=True)
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if expected and digest!=expected:raise ValueError('Source SHA256 differs from the exact published recipe')
        record={'url':url,'path':str(path.relative_to(self.root)),'size':path.stat().st_size,'sha256':digest,'expected_sha256':expected,'verified':bool(expected and digest==expected),'verification':'primary_recipe_sha256_match' if expected else 'immutable_origin_or_version_pinned_content_hash_recorded'}
        self.records.append(record)
        return path

    def metadata(self,url,name):
        return json.loads(self.fetch(url,name).read_text(encoding='utf-8'))

    def github_tree(self,repository,tag):
        reference=self.metadata('https://api.github.com/repos/'+repository+'/git/ref/tags/'+tag,'metadata/'+repository.replace('/','-')+'-'+tag+'.json')['object']
        while reference['type']=='tag':reference=self.metadata(reference['url'],'metadata/tag-'+reference['sha']+'.json')['object']
        commit=reference['sha']
        if reference['type']!='commit':raise ValueError('Release tag does not bind a commit')
        archive=self.fetch('https://codeload.github.com/'+repository+'/tar.gz/'+commit,'recipes/'+repository.replace('/','-')+'-'+commit+'.tar.gz')
        return archive,commit

    def notices(self,archive,component):
        selected=[];members=0;expanded_bytes=0
        with open_tar(archive) as source:
            for member in source:
                members+=1;expanded_bytes+=member.size
                if members>200000 or expanded_bytes>2*1024**3:raise ValueError('Source archive inventory exceeds bounded quota')
                name=PurePosixPath(member.name)
                if not member.isfile() or member.size>4*1024**2:continue
                if not re.match(r'(?i)^(?:licen[sc]e|copying|copyright|notice)(?:[._-].*)?$',name.name):continue
                if name.is_absolute() or '..' in name.parts or '\\' in member.name:raise ValueError('Unsafe notice member')
                data=source.extractfile(member).read()
                digest=hashlib.sha256(data).hexdigest()
                target=self.root/'notices'/component/(digest[:12]+'-'+name.name)
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                selected.append({'member':member.name,'notice':str(target.relative_to(self.root)),'sha256':digest})
        return selected

    def pypi_source(self,name,version):
        metadata=self.metadata('https://pypi.org/pypi/'+name+'/'+version+'/json','metadata/pypi-'+name+'-'+version+'.json')
        source=next((record for record in metadata['urls'] if record['packagetype']=='sdist'),None)
        if not source:raise ValueError('Exact PyPI source distribution missing')
        archive=self.fetch(source['url'],'python/'+source['filename'],source['digests']['sha256'])
        self.notices(archive,name)
        return {'name':name,'version':version,'source':str(archive.relative_to(self.root)),'sha256':source['digests']['sha256']}

    def save(self):
        records={}
        for record in self.records:
            record=dict(record);record['path']=record['path'].replace('\\','/')
            previous=records.get(record['path'])
            if previous and previous['sha256']!=record['sha256']:raise ValueError('Conflicting source receipts for the same path')
            if not previous or record.get('verified'):records[record['path']]=record
        (self.root/'SOURCE-RECEIPTS.json').write_text(json.dumps({'archives':sorted(records.values(),key=lambda r:r['path']),'errors':self.errors,'historical_attempts':self.historical_attempts},indent=2),encoding='utf-8')


def open_tar(path):
    if path.name.endswith('.zst'):
        import zstandard
        decoded=zstandard.ZstdDecompressor().stream_reader(path.open('rb'))
        return tarfile.open(fileobj=decoded,mode='r|')
    return tarfile.open(path,mode='r:*')


def member_text(path,suffix):
    with open_tar(path) as source:
        for member in source:
            if member.isfile() and member.name.endswith(suffix) and member.size<4*1024**2:
                return source.extractfile(member).read().decode('utf-8')
    raise ValueError('Required source recipe member missing: '+suffix)


def verify_msys_payload(collector,archive,component):
    payload={}
    with open_tar(archive) as source:
        for member in source:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:raise ValueError('Unsafe MSYS source member')
            if member.isfile():
                if member.size>128*1024**2:raise ValueError('MSYS source member exceeds bounded quota')
                payload[name.name]=source.extractfile(member).read()
    srcinfo=payload['.SRCINFO'].decode('utf-8')
    recipe=payload['PKGBUILD'].decode('utf-8')
    values={}
    for key,value in re.findall(r'^\s*(source(?:_[A-Za-z0-9_]+)?|sha256sums(?:_[A-Za-z0-9_]+)?) = (.+)$',srcinfo,re.M):values.setdefault(key,[]).append(value)
    proofs=[]
    for key,sources in values.items():
        if not key.startswith('source'):continue
        hashes=values.get('sha256sums'+key[len('source'):],[])
        if len(hashes)!=len(sources):raise ValueError('MSYS source checksum list is incomplete')
        for entry,expected in zip(sources,hashes):
            filename=entry.split('::',1)[0] if '::' in entry else PurePosixPath(urlsplit(entry).path).name
            if filename not in payload:raise ValueError('MSYS corresponding source or patch missing: '+filename)
            actual=hashlib.sha256(payload[filename]).hexdigest()
            if expected!='SKIP' and expected not in recipe:raise ValueError('SRCINFO checksum is not present in the binary-bound PKGBUILD')
            if expected!='SKIP' and actual!=expected:raise ValueError('MSYS nested source or patch differs from SRCINFO checksum')
            proof={'file':filename,'sha256':actual,'expected_sha256':expected,'verified':expected!='SKIP'}
            if entry.startswith('https://') and expected!='SKIP':
                target=collector.root/'msys2/upstream'/component/filename
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload[filename])
                collector.records.append({'url':entry,'path':str(target.relative_to(collector.root)),'size':len(payload[filename]),'sha256':actual,'expected_sha256':expected,'verified':True,'verification':'nested_SRCINFO_sha256_match'})
                if filename.endswith(('.tar.gz','.tar.xz','.tar.bz2')):collector.notices(target,component)
            proofs.append(proof)
    return proofs


def pyav_sources(collector):
    tree,commit=collector.github_tree('PyAV-Org/pyav-ffmpeg','9.0.2-1')
    recipe=member_text(tree,'/scripts/pkg.py')
    packages=[]
    for call in ast.walk(ast.parse(recipe)):
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or call.func.id!='Package':continue
        data={argument.arg:argument.value.value for argument in call.keywords if isinstance(argument.value,ast.Constant)}
        if {'name','source_url','sha256'}<=data.keys():packages.append(data)
    for package in packages:
        try:
            filename=package.get('source_filename') or PurePosixPath(urlsplit(package['source_url']).path).name
            path=collector.fetch(package['source_url'],'pyav-ffmpeg/'+package['name']+'/'+filename,package['sha256'])
            collector.notices(path,'pyav-'+package['name'])
        except Exception as error:collector.errors.append({'component':'pyav-'+package['name'],'error':type(error).__name__+': '+str(error)})
    return {'recipe_commit':commit,'recipe_archive':str(tree.relative_to(collector.root)),'packages':packages}


def msys_sources(collector,manifest):
    bases={}
    wanted=('jbigkit','gettext-runtime','libiconv','libunistring','libidn2','lcms2')
    for package in manifest['packages']:
        if not any(package['name'].endswith('-'+name) for name in wanted):continue
        bases[package['base']+'-'+package['version']]=package
    result=[]
    for name,package in bases.items():
        try:
            url='https://mirror.msys2.org/mingw/sources/'+name+'.src.tar.zst'
            path=collector.fetch(url,'msys2/'+name+'.src.tar.zst')
            binary=Path('build/windows-ocr-clean/downloads')/package['filename']
            with binary.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=package['sha256']:raise ValueError('MSYS binary differs from shipment receipt')
            buildinfo=member_text(binary,'.BUILDINFO')
            published=re.search(r'^pkgbuild_sha256sum = ([a-f0-9]{64})$',buildinfo,re.M)
            recipe=member_text(path,'/PKGBUILD')
            actual=hashlib.sha256(recipe.encode()).hexdigest()
            if not published or actual!=published[1]:raise ValueError('Source PKGBUILD does not match the binary BUILDINFO receipt')
            proofs=verify_msys_payload(collector,path,package['base'])
            result.append({'package':package['name'],'binary_version':package['version'],'source':str(path.relative_to(collector.root)),'pkgbuild_sha256':actual,'binary_sha256':package['sha256'],'source_and_patch_checksums':proofs})
            collector.notices(path,package['base'])
        except Exception as error:collector.errors.append({'component':name,'error':type(error).__name__+': '+str(error)})
    return result


def deb_control(path):
    with path.open('rb') as source:
        if source.read(8)!=b'!<arch>\n':raise ValueError('Invalid Debian archive')
        while header:=source.read(60):
            name=header[:16].decode().strip().rstrip('/');size=int(header[48:58].strip());offset=source.tell()
            if name.startswith('control.tar'):
                raw=source.read(size)
                if name.endswith('.zst'):
                    import zstandard
                    raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=4*1024**2)
                with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
                    member=next(member for member in archive if member.isfile() and PurePosixPath(member.name).name=='control')
                    text=archive.extractfile(member).read().decode()
                    return dict(re.findall(r'^([A-Za-z][A-Za-z0-9-]*): (.*)$',text,re.M))
            source.seek(offset+size+(size%2))
    raise ValueError('Debian control receipt missing')


def ubuntu_sources(collector):
    manifest=json.loads(Path('build/linux-ocr-full/runtime/COMPONENT-MANIFEST.json').read_text())
    archives={path.name:path for path in Path('build/linux-ocr-full/downloads').rglob('*.deb')}
    packages={}
    selected=re.compile(r'^(?:libacl|libasound|libatk|libatspi|libavahi|libblkid|libcairo|libdatrie|libdb5|libdbus|libelf|libfribidi|libgcrypt|libglib|libgmp|libgnutls|libgpg-error|libgraphite|libhogweed|libidn2|libjbig|libkeyutils|libmount|libnettle|libnspr|libnss|libpango|librtmp|libsensors|libssh-|libsystemd|libtasn1|libthai|libudev|libunistring)')
    for package in manifest['packages']:
        path=archives.get(package['archive'])
        if not path:raise ValueError('Exact downloaded Debian binary archive missing')
        control=deb_control(path)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=package['sha256']:raise ValueError('Downloaded Debian binary differs from shipment receipt')
        if not selected.match(control['Package']):continue
        source=re.fullmatch(r'([^ ]+)(?: \(([^)]+)\))?',control.get('Source',control['Package']))
        name=source[1];version=source[2] or control['Version']
        key=name+':'+version
        packages.setdefault(key,{'source_package':name,'source_version':version,'binaries':[]})['binaries'].append({'name':control['Package'],'version':control['Version'],'sha256':package['sha256']})
    def obtain(record):
        name,version=record['source_package'],record['source_version']
        version_without_epoch=version.split(':',1)[-1]
        prefix=name[:4] if name.startswith('lib') else name[0]
        for component in ('main','universe','restricted','multiverse'):
            base='https://archive.ubuntu.com/ubuntu/pool/'+component+'/'+prefix+'/'+name+'/'
            try:
                dsc=collector.fetch(base+name+'_'+version_without_epoch+'.dsc','ubuntu/'+name+'_'+version_without_epoch+'.dsc')
            except HTTPError as error:
                if error.code==404:continue
                raise
            text=dsc.read_text()
            block=re.search(r'^Checksums-Sha256:\n((?: [^\n]+\n)+)',text,re.M)
            if not block:raise ValueError('Ubuntu source control lacks exact SHA256 manifest')
            files=[]
            for line in block[1].splitlines():
                digest,size,filename=line.split()
                archive=collector.fetch(base+filename,'ubuntu/'+filename,digest)
                if archive.stat().st_size!=int(size):raise ValueError('Ubuntu source archive size differs from source control')
                files.append({'path':str(archive.relative_to(collector.root)),'sha256':digest,'size':int(size)})
                if filename.endswith(('.tar.gz','.tar.xz','.tar.bz2')):collector.notices(archive,'ubuntu-'+name)
            return record|{'dsc':str(dsc.relative_to(collector.root)),'source_files':files,'dsc_signature_verified':False}
        raise ValueError('Exact Ubuntu source version not found in primary archive pools')
    result=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(obtain,record):record for record in packages.values()}
        for future in as_completed(futures):
            try:result.append(future.result())
            except Exception as error:collector.errors.append({'component':futures[future]['source_package'],'error':type(error).__name__+': '+str(error)})
    return result


def python_sources(collector):
    selected={}
    for platform in ('windows','linux'):
        manifest=Path('dist')/platform/'StudyCenter-Public/THIRD-PARTY.json'
        if not manifest.exists():continue
        records=json.loads(manifest.read_text(encoding='utf-8'))
        for record in records:
            if record['name'].lower() in {'pymupdf','av','pystray','certifi','tqdm','ctranslate2','flatbuffers','tokenizers','proxy_tools'}:
                selected[record['name'].lower()]=record['version']
    import importlib.metadata
    selected['pyinstaller']=importlib.metadata.version('pyinstaller')
    git_repositories={'pystray':('moses-palmer/pystray','v0.19.5'),'ctranslate2':('OpenNMT/CTranslate2','v4.8.3'),'flatbuffers':('google/flatbuffers','v25.12.19')}
    result=[]
    for name,version in selected.items():
        try:
            if name in git_repositories:
                repository,tag=git_repositories[name]
                archive,commit=collector.github_tree(repository,tag)
                collector.notices(archive,name)
                result.append({'name':name,'version':version,'source':str(archive.relative_to(collector.root)),'commit':commit})
            else:result.append(collector.pypi_source(name,version))
        except Exception as error:collector.errors.append({'component':name,'error':type(error).__name__+': '+str(error)})
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='build/corresponding-sources');parser.add_argument('--group',choices=('pyav','msys','ubuntu','python','all'),default='all');args=parser.parse_args()
    collector=SourceCollector(args.output)
    try:
        metadata=json.loads((collector.root/'SOURCE-CORRESPONDENCE.json').read_text()) if (collector.root/'SOURCE-CORRESPONDENCE.json').exists() else {}
        if (collector.root/'SOURCE-RECEIPTS.json').exists():
            old=json.loads((collector.root/'SOURCE-RECEIPTS.json').read_text());collector.records=old['archives'];collector.errors=old['errors']
            collector.historical_attempts=old.get('historical_attempts',[])
        if args.group in ('pyav','all'):metadata['pyav']=pyav_sources(collector)
        if args.group in ('msys','all'):metadata['msys2']=msys_sources(collector,json.loads(Path('build/windows-ocr-clean/runtime/COMPONENT-MANIFEST.json').read_text()))
        if args.group in ('ubuntu','all'):metadata['ubuntu']=ubuntu_sources(collector)
        if args.group in ('python','all'):metadata['python']=python_sources(collector)
        (collector.root/'SOURCE-CORRESPONDENCE.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    finally:collector.save()
    print('Source archives:',len(collector.records),'unresolved:',len(collector.errors))
    for error in collector.errors:print(error['component'],error['error'])


if __name__=='__main__':main()
