"""Hash explicitly selected code/test trees; never inspect application data."""
import argparse
import hashlib
import json
import os
from pathlib import Path

SUFFIXES={'.py','.html','.css','.js','.json','.webp','.png','.jpg','.jpeg','.ico','.svg','.txt'}
EXCLUDED={'.git','.local','.venv','__pycache__','node_modules','profiles','data','documents','logs','cache'}


def snapshot(roots):
    result={}
    for label,root in roots.items():
        root=Path(root)
        if (not root.is_dir() or any(part.casefold() in EXCLUDED for part in root.parts)
            or any(parent.is_symlink() or parent.is_junction() for parent in (root,*root.parents))):
            raise ValueError('Code and test roots must be real directories outside data and profile trees')
        for parent,dirs,files in os.walk(root,followlinks=False):
            base=Path(parent)
            dirs[:]=[name for name in dirs if name.casefold() not in EXCLUDED and not (base/name).is_symlink() and not (base/name).is_junction()]
            for name in files:
                path=base/name
                if path.is_symlink() or path.suffix.lower() not in SUFFIXES:continue
                relative=path.relative_to(root).as_posix()
                with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                result[label+'/'+relative]=digest
    return dict(sorted(result.items()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('capture','verify'))
    parser.add_argument('--code-dir',type=Path,required=True)
    parser.add_argument('--tests-dir',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    roots={'code':args.code_dir,'tests':args.tests_dir}
    current=snapshot(roots)
    if args.mode=='capture':
        destination=args.manifest.resolve()
        if any(destination.is_relative_to(root.resolve()) for root in roots.values()):
            raise ValueError('Manifest must be outside the reference directories')
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():raise ValueError('Manifest already exists; choose a new path')
        destination.write_text(json.dumps({'format':1,'files':current},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print(f'Captured {len(current)} file hashes.')
        return 0
    saved=json.loads(args.manifest.read_text(encoding='utf-8'))
    if saved.get('format')!=1 or not isinstance(saved.get('files'),dict):raise ValueError('Unsupported manifest')
    previous=saved['files']
    changed=sorted(name for name in previous.keys()|current.keys() if previous.get(name)!=current.get(name))
    for name in changed:print(name)
    print(f'Checked {len(current)} files; {len(changed)} changed, missing or new.')
    return int(bool(changed))


if __name__=='__main__':raise SystemExit(main())
