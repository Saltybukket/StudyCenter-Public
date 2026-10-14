"""Native onedir builds with manifests, licenses, and optional own OCR components."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib

ROOT=Path(__file__).resolve().parents[1]
APP='StudyCenter-Public'


def file_hash(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def ensure_source_inputs_unchanged(snapshot):
    if any(not path.is_file() or file_hash(path)!=digest for path,digest in snapshot.items()):
        raise ValueError('Application source inputs changed during freeze; discard candidate and rebuild from a new checkpoint')


def source_inputs(source_commit):
    inputs=[path for path in (ROOT/'src'/'studycenter_public').rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc','.pyo'}]
    inputs += [ROOT/'packaging'/'frozen_entry.py',ROOT/'packaging'/'runtime_hook.py']
    if source_commit:
        actual=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        if actual!=source_commit:raise ValueError('Build source commit differs from checkout HEAD')
        changed=subprocess.check_output(['git','-C',str(ROOT),'diff',source_commit,'--name-only','--','src/studycenter_public','packaging/frozen_entry.py','packaging/runtime_hook.py'],text=True).splitlines()
        if changed:
            raise ValueError('Application source differs from the committed checkpoint')
        untracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--others','--exclude-standard','--','src/studycenter_public','packaging/frozen_entry.py','packaging/runtime_hook.py'],text=True).splitlines()
        if any('__pycache__' not in Path(name).parts and Path(name).suffix not in {'.pyc','.pyo'} for name in untracked):
            raise ValueError('Untracked application source is outside the committed checkpoint')
    return {path:file_hash(path) for path in inputs}


def write_manifest(bundle,version,source_commit=None):
    bundle=Path(bundle);files={}
    for path in sorted(bundle.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(bundle)
        if set(relative.parts)&{'browser-profiles','data','profiles','.env','.venv','.local'} or path.suffix in ('.sqlite3','.db','.dpapi'):
            raise ValueError('Runtime data or credentials must not be packaged')
        if relative.name=='BUILD-MANIFEST.json':continue
        files[relative.as_posix()]={'size':path.stat().st_size,'sha256':file_hash(path)}
    source_files={}
    for path in (ROOT/'src'/'studycenter_public').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'):
            source_files[path.relative_to(ROOT).as_posix()]=file_hash(path)
    source_fingerprint=hashlib.sha256(json.dumps(source_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest={'application':APP,'version':version,'platform':sys.platform,'python':sys.version.split()[0],'source_tree_sha256':source_fingerprint,'source_files':source_files,'files':files}
    if source_commit:
        actual=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        if source_commit!=actual:raise ValueError('Build source commit differs from checkout HEAD')
        manifest['source_git_sha']=source_commit
    (bundle/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    return manifest


def archive_linux(bundle,destination,version):
    target=Path(destination)/f'studycenter-public-{version}-linux-x86_64.tar.gz'
    with tarfile.open(target,'w:gz') as archive:
        archive.add(bundle,arcname=APP,filter=lambda member: _archive_member(member))
    return target


def _archive_member(member):
    member.uid=member.gid=0;member.uname=member.gname=''
    if member.isfile() and (PurePathName(member.name)==APP or member.name.endswith('.sh')):member.mode |= 0o755
    return member


def PurePathName(name):
    return name.rsplit('/',1)[-1]


def strip_build_artifacts(bundle):
    bundle=Path(bundle).resolve()
    for path in sorted(bundle.rglob('__pycache__'),reverse=True):
        if not path.is_relative_to(bundle):raise ValueError('Collected cache escaped bundle')
        if path.is_dir():shutil.rmtree(path)
    browsers=bundle/'components'/'browsers'
    if browsers.exists():
        for path in browsers.glob('ffmpeg-*'):
            if not path.resolve().is_relative_to(bundle) or path.is_symlink():raise ValueError('Browser helper escaped bundle')
            if path.is_dir():shutil.rmtree(path)
        for path in browsers.rglob('WidevineCdm'):
            if not path.resolve().is_relative_to(bundle) or path.is_symlink():raise ValueError('DRM helper escaped bundle')
            if path.is_dir():shutil.rmtree(path)
        # Official browser archives also contain system-installation helpers.
        # Runtime browser launches do not need them and never execute them.
        for path in browsers.rglob('*'):
            if path.is_file() and path.name.casefold() in {'setup.exe','elevation_service.exe','elevated_tracing_service.exe'}:
                path.unlink()


def copy_ocr(source,destination):
    """Runtime whitelist: never redistribute installers or uninstallation helpers."""
    source=Path(source);destination.mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        import pefile
        available={path.name.casefold():path for path in source.glob('*.dll')}
        pending=[source/'tesseract.exe'];copied=set()
        while pending:
            binary=pending.pop()
            if binary.name.casefold() in copied:continue
            copied.add(binary.name.casefold());shutil.copy2(binary,destination/binary.name)
            with pefile.PE(str(binary),fast_load=False) as image:
                for dependency in getattr(image,'DIRECTORY_ENTRY_IMPORT',[]):
                    name=dependency.dll.decode('ascii').casefold()
                    if name in available:pending.append(available[name])
        trained=destination/'tessdata';trained.mkdir(exist_ok=True)
        for language in ('eng','deu'):
            path=source/'tessdata'/(language+'.traineddata')
            if not path.is_file():raise ValueError('Missing bundled OCR language: '+language)
            shutil.copy2(path,trained/path.name)
        notices=destination/'licenses';notices.mkdir(exist_ok=True)
        if (source/'licenses').exists():shutil.copytree(source/'licenses',notices,dirs_exist_ok=True)
        if (source/'COMPONENT-MANIFEST.json').exists():shutil.copy2(source/'COMPONENT-MANIFEST.json',destination/'COMPONENT-MANIFEST.json')
        for filename in ('LICENSE','AUTHORS'):
            if (source/'doc'/filename).exists():shutil.copy2(source/'doc'/filename,notices/filename)
    else:
        # This input must already be the staged dpkg-deb runtime extraction.
        for path in source.rglob('*'):
            if path.is_file() and ('uninstall' in path.name.casefold() or path.suffix=='.deb'):raise ValueError('OCR stage contains installation tooling')
        shutil.copytree(source,destination,dirs_exist_ok=True,symlinks=True)


def collect_licenses(bundle):
    target=bundle/'licenses';target.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'LICENSE',target/'StudyCenter-Public-AGPL-3.0-only.txt')
    distributions=[]
    for distribution in sorted(importlib.metadata.distributions(),key=lambda d:d.metadata['Name'].casefold()):
        name=distribution.metadata['Name']
        if name.casefold() in ('studycenter-public','pip','setuptools','wheel','pytest','pytest-asyncio','detect-secrets','pyinstaller','pyinstaller-hooks-contrib','altgraph','packaging','pefile','pywin32-ctypes','zstandard'):continue
        copied=[]
        for file in distribution.files or []:
            if any(part.startswith('.venv') for part in file.parts):continue
            if file.name.casefold().startswith(('license','copying','copyright','notice')):
                origin=Path(distribution.locate_file(file))
                if not origin.is_file():continue
                subdir=target/name;subdir.mkdir(exist_ok=True)
                output=subdir/file.name
                if output.exists():output=subdir/(hashlib.sha256(str(file).encode()).hexdigest()[:8]+'-'+file.name)
                shutil.copy2(origin,output);copied.append(output.relative_to(bundle).as_posix())
        distributions.append({'name':name,'version':distribution.version,'license':distribution.metadata.get('License-Expression') or distribution.metadata.get('License','See included notice'),'notices':copied,'home':distribution.metadata.get('Home-page','')})
    (bundle/'THIRD-PARTY.json').write_text(json.dumps(distributions,sort_keys=True,indent=2),encoding='utf-8')


def build(ocr_dir=None,iscc=None,browsers_dir=None,source_commit=None):
    snapshot=source_inputs(source_commit)
    version=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    platform='windows' if sys.platform=='win32' else 'linux'
    destination=ROOT/'dist'/platform;destination.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name',APP,'--paths',str(ROOT/'src'),'--distpath',str(destination),'--workpath',str(ROOT/'build'/platform),'--specpath',str(ROOT/'build'/platform), '--collect-all','playwright','--collect-submodules','uvicorn','--collect-all','keyring','--collect-data','studycenter_public','--collect-all','tzdata']
    command += ['--runtime-hook',str(ROOT/'packaging'/'runtime_hook.py')]
    command += ['--optimize','2','--exclude-module','PyInstaller','--exclude-module','pytest','--exclude-module','_pytest','--exclude-module','pip']
    for component in ('faster_whisper','av','tokenizers'):
        command += ['--collect-all',component]
    command += ['--collect-binaries','ctranslate2','--collect-binaries','onnxruntime','--hidden-import','ctranslate2.models','--hidden-import','onnxruntime.capi.onnxruntime_pybind11_state']
    if sys.platform=='win32':command += ['--windowed','--collect-all','webview','--collect-all','pystray']
    command += [str(ROOT/'packaging'/'frozen_entry.py')]
    environment=os.environ.copy()
    environment['PYTHONPATH']=str(ROOT/'src')
    environment['PYINSTALLER_CONFIG_DIR']=str(ROOT/'build'/'pyinstaller-cache'/platform)
    own_cache=ROOT/'build'/'component-cache'/platform
    environment['XDG_CACHE_HOME']=str(own_cache/'xdg')
    environment['HF_HOME']=str(own_cache/'huggingface')
    environment['HF_HUB_CACHE']=str(own_cache/'huggingface'/'hub')
    environment['HF_XET_CACHE']=str(own_cache/'huggingface'/'xet')
    environment['HF_ASSETS_CACHE']=str(own_cache/'huggingface'/'assets')
    environment['HF_HUB_DISABLE_XET']='1'
    environment['HF_HUB_DISABLE_TELEMETRY']='1'
    environment['HF_HUB_OFFLINE']='1'
    subprocess.run(command,cwd=ROOT,env=environment,check=True)
    bundle=destination/APP
    for relative in ('web/static','web/templates','resources'):
        origin=ROOT/'src'/'studycenter_public'/relative
        if origin.exists():shutil.copytree(origin,bundle/'_internal'/'studycenter_public'/relative,dirs_exist_ok=True)
    if ocr_dir:
        copy_ocr(ocr_dir,bundle/'components'/'ocr')
    if browsers_dir:
        shutil.copytree(browsers_dir,bundle/'components'/'browsers',dirs_exist_ok=True,ignore=shutil.ignore_patterns('.links'))
    for filename in ('README.md','LICENSE'):
        shutil.copy2(ROOT/filename,bundle/filename)
    if sys.platform!='win32':
        for filename in ('start.sh','install-desktop.sh'):
            shutil.copy2(ROOT/'packaging'/'linux'/filename,bundle/filename);(bundle/filename).chmod(0o755)
    ensure_source_inputs_unchanged(snapshot)
    strip_build_artifacts(bundle)
    collect_licenses(bundle);write_manifest(bundle,version,source_commit)
    if sys.platform=='win32' and iscc:
        subprocess.run([str(iscc),'/DAppVersion='+version,'/DBundleDir='+str(bundle),'/DOutputDir='+str(destination),str(ROOT/'packaging'/'windows'/'installer.iss')],check=True)
    elif sys.platform!='win32':archive_linux(bundle,destination,version)
    print(json.dumps({'bundle':str(bundle),'version':version,'platform':platform}))
    return bundle


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ocr-dir',type=Path);parser.add_argument('--iscc',type=Path);parser.add_argument('--browsers-dir',type=Path);parser.add_argument('--source-commit')
    args=parser.parse_args();build(args.ocr_dir,args.iscc,args.browsers_dir,args.source_commit)

if __name__=='__main__':main()
