"""Content gate. Reports only filenames/rules, never matched secret values."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.env', '.local', '.venv', 'browser-profiles', '__pycache__'}
RULES = {
    'credential': re.compile(rb'(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
    'personal path': re.compile(rb'(?:[A-Za-z]:[\\/](?:Users|Projects)[\\/]|/home/[a-z][a-z0-9_-]+/)', re.I),
    'private identity': re.compile(rb'(?:CampusBoard|campusboard|CAMPUSBOARD_|Saltybukket/CampusBoard|OpenAI\.Codex_[A-Za-z0-9]+)'),
    'personal mail': re.compile(rb'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@(?:student\.)?tugraz\.at', re.I),
    'private endpoint': re.compile(rb'(?:localhost|127\.0\.0\.1):8000\b|ngrok(?:-free)?\.(?:app|dev)|trycloudflare\.com', re.I),
}

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])

def check(name, content, artifacts=False):
    problems = []
    parts = name.replace('\\', '/').split('/')
    if any(p in FORBIDDEN_PARTS or p.startswith('.env.') for p in parts) or name.endswith(('.sqlite3', '.db', '.dpapi')):
        problems.append('forbidden data file')
    variants=[content]
    if artifacts:
        # Decode both UTF-16 alignments and preserve NUL string terminators.
        # Removing every NUL would join unrelated PE strings into invented keys.
        variants.extend(content[offset:].decode('utf-16-le',errors='replace').encode('utf-8') for offset in (0,1))
    for rule, pattern in RULES.items():
        if any(pattern.search(value) for value in variants): problems.append(rule)
    return [(name, p) for p in problems]

def archive_members(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for member in z.infolist():
                if not member.is_dir():
                    with z.open(member) as file:yield from file_chunks(member.filename, file)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as t:
            for member in t:
                if member.isfile():
                    with t.extractfile(member) as file:yield from file_chunks(member.name, file)
    else:
        raise ValueError('Extract this artifact first, then scan the directory.')

def file_chunks(name, file):
    # Bounded memory even for embedded browsers. Overlap catches split matches.
    overlap = b''
    while block := file.read(4 * 1024 * 1024):
        content = overlap + block
        yield name, content
        overlap = content[-65536:]

def directory_members(directory):
    for file in directory.rglob('*'):
        if file.is_symlink() and not file.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Artifact link escapes its own directory: '+str(file.relative_to(directory)))
        if file.is_file():
            with file.open('rb') as stream:yield from file_chunks(str(file.relative_to(directory)), stream)

def reviewed_artifact_matches(path, failures, reviews_path):
    """Classify only explicit upstream findings in an identical reviewed file."""
    if not reviews_path:return sorted(set(failures)), []
    records=json.loads(reviews_path.read_text(encoding='utf-8'))
    unresolved=[];reviewed=[]
    for name,rule in sorted(set(failures)):
        relative=name.replace('\\','/')
        record=records.get(relative,{})
        if rule not in {'credential','personal path','private endpoint'} or rule not in record.get('rules',[]) or not record.get('reason') or not record.get('origin'):
            unresolved.append((name,rule));continue
        if path.is_dir():
            file=path/relative
            if not file.resolve().is_relative_to(path.resolve()):raise ValueError('Review path escapes artifact')
            with file.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive,archive.open(name) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        else:
            with tarfile.open(path) as archive,archive.extractfile(name) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest==record.get('sha256'):reviewed.append((name,rule))
        else:unresolved.append((name,rule))
    return unresolved, reviewed

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--staged', action='store_true')
    p.add_argument('--outgoing', metavar='BASE')
    p.add_argument('--artifact', type=Path)
    p.add_argument('--artifact-reviews', type=Path, help='Exact file hashes of manually reviewed upstream constants')
    a = p.parse_args()
    entries = []
    if a.artifact:
        if a.artifact.is_dir():
            entries = directory_members(a.artifact)
        else: entries = archive_members(a.artifact)
    elif a.outgoing:
        for commit in git('rev-list', f'{a.outgoing}..HEAD').decode().splitlines():
            for name in git('ls-tree','-r','--name-only',commit).decode().splitlines():
                entries.append((name,git('show',f'{commit}:{name}')))
    else:
        names = git('diff','--cached','--name-only','--diff-filter=ACMR') if a.staged else git('ls-files')
        for name in names.decode().splitlines():
            entries.append((name,git('show',f':{name}') if a.staged else (ROOT/name).read_bytes()))
    failures = []
    checked = 0
    for name, content in entries:
        checked += 1
        # The scanner necessarily contains deny-pattern examples, not app data.
        if name == 'scripts/check_public.py':
            failures.extend((name,'credential') for _ in RULES['credential'].finditer(content))
        else: failures.extend(check(name,content,bool(a.artifact)))
    raw_count=len(set(failures));reviewed=[]
    if a.artifact:failures,reviewed=reviewed_artifact_matches(a.artifact,failures,a.artifact_reviews)
    elif a.artifact_reviews:raise ValueError('Artifact reviews never exempt public source files')
    for name,rule in sorted(set(failures)): print(f'{name}: {rule}')
    print(f'Checked {checked} file versions/chunks; {raw_count} raw findings; {len(reviewed)} exact reviewed upstream findings; {len(set(failures))} unresolved findings.')
    return bool(failures)

if __name__ == '__main__': sys.exit(main())
