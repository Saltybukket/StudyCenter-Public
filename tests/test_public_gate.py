import io
import hashlib
import json
import subprocess
import sys
import pytest
from scripts.check_public import check, file_chunks, reviewed_artifact_matches, source_scanner_example


def test_native_binaries_do_not_bypass_private_path_checks():
    private_path = ('C:' + '/' + 'Users' + '/' + 'synthetic' + '/example').encode()
    for suffix in ('.exe', '.dll', '.so', '.pyc'):
        assert ('component' + suffix, 'personal path') in check('component' + suffix, private_path, True)
        wide = private_path.decode().encode('utf-16-le')
        assert ('component' + suffix, 'personal path') in check('component' + suffix, wide, True)
    fragments=('s'+'k-'+'part').encode('utf-16-le') + b'\0\0' + ('x'*30).encode('utf-16-le')
    assert ('component.exe','credential') not in check('component.exe', fragments, True)


def test_chunk_boundary_and_environment_variants_are_checked():
    marker = ('C:' + '/' + 'Users' + '/' + 'synthetic' + '/example').encode()
    content = b'x' * (4 * 1024 * 1024 - 3) + marker
    assert any(check(name, block, True) for name, block in file_chunks('component.dll', io.BytesIO(content)))
    assert check('package/' + '.env' + '.local', b'generic', True)


def test_reviewed_vendor_constant_is_bound_to_file_hash_and_rule(tmp_path):
    component=tmp_path/'component.dll';component.write_bytes(b'upstream synthetic constant')
    reviews=tmp_path/'reviews.json'
    reviews.write_text(json.dumps({'component.dll':{'sha256':hashlib.sha256(component.read_bytes()).hexdigest(),'rules':['personal path'],'reason':'Synthetic reviewed metadata','origin':'https://example.invalid/upstream'}}))
    findings=[('component.dll','personal path')]
    assert reviewed_artifact_matches(tmp_path,findings,reviews)==([],findings)
    assert reviewed_artifact_matches(tmp_path,[('component.dll','forbidden data file')],reviews)[0]
    component.write_bytes(b'changed unreviewed bytes')
    assert reviewed_artifact_matches(tmp_path,findings,reviews)==(findings,[])
    first=json.loads(reviews.read_text())['component.dll']
    second=first|{'sha256':hashlib.sha256(component.read_bytes()).hexdigest()}
    reviews.write_text(json.dumps({'component.dll':[first,second]}))
    assert reviewed_artifact_matches(tmp_path,findings,reviews)==([],findings)
    component.write_bytes(b'another unreviewed version')
    assert reviewed_artifact_matches(tmp_path,findings,reviews)==(findings,[])


def test_source_archive_scanner_exception_requires_exact_committed_bytes():
    expected = b'public scanner deny pattern examples'
    assert source_scanner_example('scripts/check_public.py', expected, expected)
    assert source_scanner_example('StudyCenter-Public/scripts/check_public.py', expected, expected)
    assert not source_scanner_example('other/scripts/check_public.py', expected, expected)
    assert not source_scanner_example('StudyCenter-Public/scripts/check_public.py', expected + b' altered', expected)


def test_mail_domain_prefilter_preserves_case_wide_strings_and_split_matches():
    mail = b'OWNER' + b'@' + b'TUGRAZ' + b'.AT'
    for raw in (mail, b'\xff' + mail.decode().encode('utf-16-le')):
        assert ('component.bin', 'personal mail') in check('component.bin', raw, True)
    raw = b'\0' * (4 * 1024 * 1024 - 9) + mail
    assert any(('component.bin', 'personal mail') in check(name, content, True)
               for name, content in file_chunks('component.bin', io.BytesIO(raw)))


def test_impossible_mail_domain_does_not_enter_regex_scan(monkeypatch):
    from scripts.check_public import RULES
    class Guard:
        def search(self, value):
            raise AssertionError('Impossible mail domain entered regex scan')
    monkeypatch.setitem(RULES, 'personal mail', Guard())
    assert check('component.bin', b'A' * 100000, True) == []


def test_additional_private_key_headers_are_detected_in_native_artifacts():
    for kind in ('DSA', 'ENCRYPTED'):
        header = ('-----' + 'BEGIN ' + kind + ' PRIVATE' + ' KEY-----').encode()
        for raw in (header, b'\xff' + header.decode().encode('utf-16-le')):
            assert ('component.dll', 'credential') in check('component.dll', raw, True)
        split = b'x' * (4 * 1024 * 1024 - 7) + header
        assert any(('component.dll', 'credential') in check(name, block, True)
                   for name, block in file_chunks('component.dll', io.BytesIO(split)))


@pytest.mark.parametrize('mode',['staged','outgoing'])
@pytest.mark.parametrize('marker,rule',[
    (('C:'+'/'+'Users'+'/'+'synthetic'+'/file').encode(),'personal path'),
    (('Campus'+'Board').encode(),'private identity'),
    (('example'+'@'+'student.'+'tugraz.at').encode(),'personal mail'),
    (('127.0.0.1'+':'+'8000').encode(),'private endpoint'),
])
def test_git_gate_scans_new_content_in_scanner_itself(monkeypatch,capsys,mode,marker,rule):
    from scripts import check_public as gate
    committed=subprocess.check_output(['git','show','HEAD:scripts/check_public.py'])
    altered=committed+b'\n# synthetic added value: '+marker+b'\n'
    def fake_git(*args):
        if args==('show','HEAD:scripts/check_public.py'):return committed
        if args[0]=='diff':return b'scripts/check_public.py\n'
        if args[0]=='rev-list':return b'syntheticcommit\n'
        if args[0]=='ls-tree':return b'scripts/check_public.py\n'
        if args[0]=='show':return altered
        raise AssertionError(args)
    monkeypatch.setattr(gate,'git',fake_git)
    monkeypatch.setattr(sys,'argv',['check_public.py','--staged'] if mode=='staged' else ['check_public.py','--outgoing','BASE'])
    assert gate.main() is True
    assert rule in capsys.readouterr().out
