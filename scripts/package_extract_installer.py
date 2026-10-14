"""Read-only Inno content extraction with a pinned portable upstream unpacker."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

UNPACKER_SHA256='94377351f03257b06e221285b16d41108040151ab672b96faf1d3d874a7d0a32'
ARCHIVE_SHA256='f2f037fdbc63de31248efae9ccb294398d160dc0cbad5f36d14c2f159e17bbf5'
UPSTREAM='https://www.rathlev-home.de/tools/download/innounp-2.zip'


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def extract(installer,unpacker,destination):
    installer=Path(installer).resolve();unpacker=Path(unpacker).resolve();destination=Path(destination).resolve()
    if digest(unpacker)!=UNPACKER_SHA256:raise ValueError('Portable unpacker differs from the reviewed upstream binary')
    if destination.exists():raise ValueError('Choose a new dedicated extraction directory')
    destination.mkdir(parents=True)
    # Executes only the archive decoder. The installer is an input file.
    subprocess.run([str(unpacker),'-x','-b','-q','-a','-m','-d'+str(destination),str(installer)],check=True,timeout=300)
    application=destination/'{app}'
    manifest=json.loads((application/'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    expected=set(manifest['files'])|{'BUILD-MANIFEST.json'}
    actual={p.relative_to(application).as_posix() for p in application.rglob('*') if p.is_file()}
    if expected!=actual:raise ValueError('Extracted installer inventory differs from build manifest')
    for name,record in manifest['files'].items():
        path=application/name
        if path.stat().st_size!=record['size'] or digest(path)!=record['sha256']:raise ValueError('Extracted file hash differs: '+name)
    result={'installer_sha256':digest(installer),'unpacker_sha256':UNPACKER_SHA256,'unpacker_archive_sha256':ARCHIVE_SHA256,'unpacker_upstream':UPSTREAM,'verified_application_files':len(actual),'source_git_sha':manifest.get('source_git_sha'),'extraction_only':True}
    (destination/'EXTRACTION-VERIFICATION.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return application,result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('installer',type=Path);parser.add_argument('--unpacker',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();application,result=extract(args.installer,args.unpacker,args.output);print(json.dumps(result))
