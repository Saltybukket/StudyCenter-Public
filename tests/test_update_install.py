import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from studycenter_public import update_install as update


def archive(path, extra=None):
    files = {'start.sh': b'#!/bin/sh\n# update-recover.py\n', 'LICENSE': b'license', 'README.md': b'readme',
             'setup.py': b'python', 'backup_core.py': b'backup', 'slim-manifest.json': b'{}'}
    with tarfile.open(path, 'w:gz') as out:
        for name, data in files.items():
            entry = tarfile.TarInfo('StudyCenter-Public/'+name)
            entry.size = len(data)
            out.addfile(entry, io.BytesIO(data))
        if extra:
            out.addfile(extra)
    return files


def test_extracts_only_expected_regular_files(tmp_path):
    source = tmp_path/'release.tar.gz'; expected = archive(source)
    stage = tmp_path/'stage'; stage.mkdir()
    update._extract_slim(source, stage)
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == expected


@pytest.mark.parametrize('name,kind', [('../outside', tarfile.REGTYPE), ('StudyCenter-Public/link', tarfile.SYMTYPE),
                                      ('StudyCenter-Public/start.sh', tarfile.LNKTYPE)])
def test_rejects_traversal_links_and_duplicates(tmp_path, name, kind):
    source = tmp_path/'release.tar.gz'
    entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = '../outside'
    archive(source, entry)
    stage = tmp_path/'stage'; stage.mkdir()
    with pytest.raises(update.UpdateInstallError): update._extract_slim(source, stage)
    assert not (tmp_path/'outside').exists()


def test_preflight_rejects_changed_digest_without_stopping_or_staging(tmp_path, monkeypatch):
    root = tmp_path/'app'; root.mkdir(); source = tmp_path/'release.tar.gz'; archive(source)
    data = tmp_path/'data'; data.mkdir()
    paths = SimpleNamespace(data=data, config=data)
    monkeypatch.setattr(update, '_layout', lambda paths: (root, True, {}))
    monkeypatch.setattr(update.sys, 'platform', 'linux')
    with pytest.raises(update.UpdateInstallError, match='Prüfsumme'):
        update.prepare_handoff(paths, {'platform': 'linux-x86_64', 'path': str(source), 'sha256': '0'*64, 'installer_compatible': True})
    assert not list(tmp_path.glob('.studycenter-update-*'))


def test_handoff_detects_staged_mutation_before_spawning(tmp_path, monkeypatch):
    root = tmp_path/'app'; root.mkdir(); source = tmp_path/'release.tar.gz'; files = archive(source)
    for name in files: (root/name).write_bytes(b'previous')
    data = tmp_path/'data'; data.mkdir(); paths = SimpleNamespace(data=data, config=data)
    monkeypatch.setattr(update, '_layout', lambda paths: (root, True, {}))
    monkeypatch.setattr(update, 'restrict_permissions', lambda p: None)
    monkeypatch.setattr(update.sys, 'platform', 'linux')
    record = {'platform': 'linux-x86_64', 'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'installer_compatible': True}
    prepared = update.prepare_handoff(paths, record)
    (Path(prepared['stage'])/'release'/'setup.py').write_text('tampered')
    with pytest.raises(update.UpdateInstallError, match='verändert'): update.launch_handoff(prepared)
    assert (root/'setup.py').read_bytes() == b'previous'


def test_checkout_has_no_update_support(monkeypatch):
    monkeypatch.delenv('STUDYCENTER_PUBLIC_INSTALL_ROOT', raising=False)
    monkeypatch.setattr(update.sys, 'frozen', False, raising=False)
    ok, reason = update.installation_support(SimpleNamespace())
    assert not ok and 'Checkout' in reason
