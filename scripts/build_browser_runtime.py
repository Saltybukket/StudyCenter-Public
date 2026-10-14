"""Stage unbranded official Chromium snapshots beside a licensed headless shell."""
import argparse
import base64
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import urllib.parse
import urllib.request
import zipfile

PLATFORMS={
    'windows':{'directory':'Win_x64','revision':'1718660','archive':'chrome-win.zip','folder':'chrome-win','target':'chrome-win64','size':362938758,'sha256':'bf256383a76758519353122d560f9bf260a8a4dc4299461f0aa74c71c8520ff9','source_git_sha':'eb240cfb93dd5d77b897a6f0a8e15907ff8f669c'},
    'linux':{'directory':'Linux_x64','revision':'1718777','archive':'chrome-linux.zip','folder':'chrome-linux','target':'chrome-linux64','size':251501764,'sha256':'73fa8a59532ca84586601926c1d6364c0de520b8676f77c7589685280d730ae3','source_git_sha':'2a19df679726c9ff635e95fd177ccbc1f69c1d61'},
}
BASE='https://storage.googleapis.com/chromium-browser-snapshots/'


def get_json(url):
    with urllib.request.urlopen(url,timeout=60) as response:return json.load(response)


def extract_snapshot(archive,target,folder,platform):
    target=Path(target).resolve()
    with zipfile.ZipFile(archive) as contents:
        for member in contents.infolist():
            path=PurePosixPath(member.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in member.filename:raise ValueError('Unsafe browser archive path')
            if not path.parts or path.parts[0]!=folder:raise ValueError('Unexpected browser archive root')
            if ((member.external_attr>>16)&0o170000)==0o120000:raise ValueError('Snapshot archive contains a symlink')
            if member.is_dir():continue
            destination=target.joinpath(*path.parts[1:]);destination.parent.mkdir(parents=True,exist_ok=True)
            with contents.open(member) as source,destination.open('wb') as sink:shutil.copyfileobj(source,sink)
            if platform=='linux':destination.chmod((member.external_attr>>16)&0o777 or 0o644)


def stage(platform,original,output):
    item=PLATFORMS[platform];output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    runtime=output/'runtime';runtime.mkdir(exist_ok=True);original=Path(original).resolve()
    for path in original.iterdir():
        if path.name.startswith('chromium_headless_shell-') or path.name.startswith('winldd-'):
            shutil.copytree(path,runtime/path.name,dirs_exist_ok=True)
    object_name='/'.join((item['directory'],item['revision'],item['archive']))
    metadata=get_json('https://storage.googleapis.com/storage/v1/b/chromium-browser-snapshots/o/'+urllib.parse.quote(object_name,safe=''))
    origin=BASE+object_name;archive=output/item['archive']
    if not archive.exists():
        with urllib.request.urlopen(origin,timeout=90) as response,archive.open('wb') as target:
            size=0
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>1_000_000_000:raise ValueError('Snapshot archive exceeds supported bound')
                target.write(block)
    with archive.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    with archive.open('rb') as file:md5=base64.b64encode(hashlib.file_digest(file,'md5').digest()).decode()
    if archive.stat().st_size!=int(metadata['size']) or md5!=metadata['md5Hash'] or archive.stat().st_size!=item['size'] or digest!=item['sha256']:raise ValueError('Pinned official snapshot checksum/size mismatch')
    revisions=get_json(BASE+item['directory']+'/'+item['revision']+'/REVISIONS')
    if revisions['got_revision']!=item['source_git_sha']:raise ValueError('Pinned snapshot source revision changed')
    target=runtime/'chromium-1243'/item['target'];target.mkdir(parents=True,exist_ok=True)
    extract_snapshot(archive,target,item['folder'],platform)
    # Runtime uses this tree as components/browsers. Remove helpers in place.
    for helper in target.rglob('WidevineCdm'):
        if not helper.resolve().is_relative_to(runtime):raise ValueError('Browser helper escaped own stage')
        if helper.is_dir():shutil.rmtree(helper)
    for helper in target.rglob('*'):
        if helper.is_file() and helper.name.casefold() in {'setup.exe','elevation_service.exe','elevated_tracing_service.exe','interactive_ui_tests.exe'}:helper.unlink()
    license_url='https://chromium.googlesource.com/chromium/src/+/'+revisions['got_revision']+'/LICENSE?format=TEXT'
    with urllib.request.urlopen(license_url,timeout=60) as response:license_data=base64.b64decode(response.read())
    if b'Redistribution and use in source and binary forms' not in license_data:raise ValueError('Snapshot source license does not contain the expected BSD grant')
    (target/'LICENSE.chromium').write_bytes(license_data)
    manifest={'component':'Unbranded Chromium official snapshot','platform':platform,'revision':item['revision'],'origin':origin,'archive_sha256':digest,'archive_size':archive.stat().st_size,'upstream_md5_base64':md5,'source_git_sha':revisions['got_revision'],'source_license_origin':license_url,'source_revisions':revisions,'playwright_runtime_layout_revision':'1243','headless_shell_preserved':True,'video_drm_helpers_removed':True}
    (runtime/'UNBRANDED-MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest));return runtime


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--platform',choices=PLATFORMS,required=True);parser.add_argument('--original',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();stage(args.platform,args.original,args.output)
