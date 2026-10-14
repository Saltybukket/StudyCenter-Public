"""Download and extract OCR runtime packages; never install or configure host packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

BROWSER_PACKAGES=['libnspr4','libnss3','libasound2t64','libatk1.0-0t64','libatk-bridge2.0-0t64','libatspi2.0-0t64','libcups2t64','libxcomposite1','libxdamage1','libxfixes3','libxrandr2','libgbm1','libxkbcommon0','libdbus-1-3','libudev1']


def isolated_apt(directory):
    """Do not inherit host configuration fragments, hooks, status, or APT_CONFIG."""
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    lists=directory/'lists';cache=directory/'cache';parts=directory/'empty-parts'
    lists.mkdir(exist_ok=True);(lists/'partial').mkdir(exist_ok=True);cache.mkdir(exist_ok=True);parts.mkdir(exist_ok=True)
    empty=directory/'empty.conf';empty.write_text('',encoding='ascii')
    initial=directory/'initial.conf'
    # APT_CONFIG is parsed before host fragments; command-line -o is too late.
    initial.write_text('Dir::Etc::Parts '+json.dumps(str(parts))+';\nDir::Etc::main '+json.dumps(str(empty))+';\n#clear APT::Update;\n#clear DPkg::Pre-Install-Pkgs;\n#clear DPkg::Post-Invoke;\n',encoding='ascii')
    status=directory/'status';status.write_text('',encoding='ascii')
    sources=directory/'sources.list';sources.write_text('deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble main universe\ndeb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble-updates main universe\ndeb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://security.ubuntu.com/ubuntu noble-security main universe\n',encoding='ascii')
    options=['-o','Dir::State='+str(directory),'-o','Dir::State::Lists='+str(lists),'-o','Dir::State::status='+str(status),'-o','Dir::Cache='+str(cache),'-o','Dir::Etc::main='+str(empty),'-o','Dir::Etc::parts='+str(parts),'-o','Dir::Etc::sourcelist='+str(sources),'-o','Dir::Etc::sourceparts=-','-o','APT::Get::List-Cleanup=0','-o','Debug::NoLocking=1','-o','Acquire::Languages=none','-o','Acquire::IndexTargets::deb::CNF::DefaultEnabled=false','-o','Acquire::IndexTargets::deb::DEP-11::DefaultEnabled=false']
    environment=os.environ.copy();environment['APT_CONFIG']=str(initial)
    return options,environment


def stage_runtime(root):
    if sys.platform!='linux':raise RuntimeError('Run this portable extraction builder on Linux')
    root=Path(root).resolve();downloads=root/'downloads'/uuid4().hex;downloads.mkdir(parents=True,exist_ok=True)
    extracted=root/'extracted'/uuid4().hex;extracted.mkdir(parents=True,exist_ok=True)
    staged=root/'runtime';staged.mkdir(exist_ok=True)
    # APT requires POSIX fchmod, unavailable on some mounted Windows filesystems.
    # Keep all isolated metadata in one owned temporary ext4 directory.
    apt_temporary=tempfile.TemporaryDirectory(prefix='studycenter-public-build-apt-')
    options,apt_environment=isolated_apt(apt_temporary.name)
    subprocess.run(['apt-get',*options,'update'],check=True,env=apt_environment)
    output=subprocess.check_output(['apt-cache',*options,'depends','--recurse','--no-recommends','--no-suggests','--no-conflicts','--no-breaks','--no-replaces','--no-enhances','tesseract-ocr','tesseract-ocr-deu',*BROWSER_PACKAGES],text=True,env=apt_environment)
    packages={line.strip() for line in output.splitlines() if line and not line[0].isspace() and re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::amd64)?',line.strip())}
    packages.discard('libc6');packages.discard('libc-bin')
    packages={p for p in packages if p.startswith('lib') or p.startswith('tesseract-ocr') or p.startswith('gcc-')}
    subprocess.run(['apt-get',*options,'download',*sorted(packages)],cwd=downloads,check=True,env=apt_environment)
    manifest=[]
    for archive in sorted(downloads.glob('*.deb')):
        subprocess.run(['dpkg-deb','-x',str(archive),str(extracted)],check=True)
        metadata=subprocess.check_output(['dpkg-deb','-f',str(archive),'Package','Version'],text=True)
        with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        manifest.append({'archive':archive.name,'size':archive.stat().st_size,'sha256':digest,'package_version':metadata.strip(),'origin':'https://archive.ubuntu.com/ubuntu/','sources':'https://packages.ubuntu.com/noble/','licenses':'usr/share/doc/<package>/copyright'})
    binary=staged/'usr/bin';binary.mkdir(parents=True,exist_ok=True)
    shutil.copy2(extracted/'usr/bin/tesseract',binary/'tesseract');(binary/'tesseract').chmod(0o755)
    for relative in ('usr/lib/x86_64-linux-gnu','lib/x86_64-linux-gnu'):
        source=extracted/relative
        if not source.exists():continue
        target=staged/'usr/lib/x86_64-linux-gnu';target.mkdir(parents=True,exist_ok=True)
        for library in source.glob('*.so*'):
            if library.is_symlink():
                resolved=library.resolve()
                if not resolved.is_relative_to(extracted):raise ValueError('Archive symlink references host files')
                shutil.copy2(resolved,target/library.name)
            elif library.is_file():shutil.copy2(library,target/library.name)
    data=staged/'usr/share/tesseract-ocr/5/tessdata';data.mkdir(parents=True,exist_ok=True)
    for language in ('eng','deu'):
        shutil.copy2(extracted/'usr/share/tesseract-ocr/5/tessdata'/(language+'.traineddata'),data/(language+'.traineddata'))
    notices=staged/'licenses';notices.mkdir(exist_ok=True)
    for notice in (extracted/'usr/share/doc').glob('*/copyright'):
        shutil.copy2(notice,notices/(notice.parent.name+'-copyright.txt'))
    (staged/'COMPONENT-MANIFEST.json').write_text(json.dumps({'component':'Tesseract portable runtime','distribution':'Ubuntu 24.04 amd64','packages':manifest},indent=2),encoding='utf-8')
    print(staged)
    apt_temporary.cleanup()
    return staged

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('build/linux-ocr'))
    stage_runtime(parser.parse_args().output)
