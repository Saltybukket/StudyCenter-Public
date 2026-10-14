"""Content gate. Reports only filenames/rules, never matched secret values."""
from __future__ import annotations
import argparse
import io
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
    'personal mail': re.compile(rb'[A-Za-z0-9._%+-]+@(?:student\.)?tugraz\.at', re.I),
    'private endpoint': re.compile(rb'(?:localhost|127\.0\.0\.1):8000\b|ngrok(?:-free)?\.(?:app|dev)|trycloudflare\.com', re.I),
}

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])

def check(name, content, artifacts=False):
    problems = []
    parts = Path(name).parts
    if any(p in FORBIDDEN_PARTS for p in parts) or name.endswith(('.sqlite3', '.db', '.dpapi')):
        problems.append('forbidden data file')
    # Compiled runtime binaries have no reliable text encoding; credentials still scan.
    for rule, pattern in RULES.items():
        if artifacts and rule == 'personal path' and name.endswith(('.pyc','.dll','.so','.exe')):
            continue
        if pattern.search(content): problems.append(rule)
    return [(name, p) for p in problems]

def archive_members(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for member in z.infolist():
                if not member.is_dir(): yield member.filename, z.read(member)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as t:
            for member in t:
                if member.isfile(): yield member.name, t.extractfile(member).read()
    else:
        raise ValueError('Extract this artifact first, then scan the directory.')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--staged', action='store_true')
    p.add_argument('--outgoing', metavar='BASE')
    p.add_argument('--artifact', type=Path)
    a = p.parse_args()
    entries = []
    if a.artifact:
        if a.artifact.is_dir():
            entries = [(str(f.relative_to(a.artifact)), f.read_bytes()) for f in a.artifact.rglob('*') if f.is_file()]
        else: entries = list(archive_members(a.artifact))
    elif a.outgoing:
        for commit in git('rev-list', f'{a.outgoing}..HEAD').decode().splitlines():
            for name in git('ls-tree','-r','--name-only',commit).decode().splitlines():
                entries.append((name,git('show',f'{commit}:{name}')))
    else:
        names = git('diff','--cached','--name-only','--diff-filter=ACMR') if a.staged else git('ls-files')
        for name in names.decode().splitlines():
            entries.append((name,git('show',f':{name}') if a.staged else (ROOT/name).read_bytes()))
    failures = []
    for name, content in entries:
        # The scanner necessarily contains deny-pattern examples, not app data.
        if name == 'scripts/check_public.py':
            failures.extend((name,'credential') for _ in RULES['credential'].finditer(content))
        else: failures.extend(check(name,content,bool(a.artifact)))
    for name,rule in sorted(set(failures)): print(f'{name}: {rule}')
    print(f'Checked {len(entries)} file versions; {len(failures)} findings.')
    return bool(failures)

if __name__ == '__main__': sys.exit(main())
