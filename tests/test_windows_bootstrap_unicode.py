"""Exercise the exact Windows bootstrap extraction with synthetic ZIPs."""
import os
from pathlib import Path
import subprocess
import sys
import warnings
from zipfile import ZipFile
import importlib

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(('label', 'expected'), [('&Next >', 'next'), ('&Weiter >', 'weiter'),
                                                ('&Install', 'install'), ('Cancel', 'cancel'),
                                                ('< &Back', '< back')])
def test_installer_navigation_label_matches_real_button_captions(monkeypatch, label, expected):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    driver = importlib.import_module('scripts.smoke_windows_cancellation')
    assert driver.Win32Wizard.navigation_label(label) == expected


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows PowerShell')
@pytest.mark.parametrize('scenario', ['valid', 'duplicate', 'existing', 'junction'])
def test_pinned_uv_extraction_handles_unicode_and_refuses_unsafe_cases(tmp_path, scenario):
    directory = tmp_path/'synthetic 学習 ä'
    directory.mkdir()
    archive = directory/'own archive.zip'
    target = directory/'own tools'/'uv.exe'
    target.parent.mkdir()
    foreign = tmp_path/'foreign'
    foreign.mkdir()
    if scenario == 'junction':
        import _winapi
        junction = directory/'redirected'
        _winapi.CreateJunction(str(foreign), str(junction))
        target = junction/'uv.exe'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with ZipFile(archive, 'w') as out:
            out.writestr('uv.exe', b'synthetic executable bytes')
            out.writestr('../foreign.txt', b'must never be extracted')
            if scenario == 'duplicate':
                out.writestr('uv.exe', b'duplicate')
    if scenario == 'existing':
        target.write_bytes(b'preexisting own sentinel')
    line = next(line for line in (ROOT/'start.cmd').read_text(encoding='utf-8').splitlines()
                if 'System.IO.Compression.ZipFile' in line or '[IO.Compression.ZipFile]' in line)
    command = line.split(' -Command ', 1)[1].strip()
    assert command.startswith('"') and command.endswith('"')
    env = dict(os.environ, STUDYCENTER_PUBLIC_SETUP_ARCHIVE=str(archive), STUDYCENTER_PUBLIC_SETUP_UV=str(target))
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-Command', command[1:-1]],
                            env=env, capture_output=True, text=True, timeout=30)
    if scenario == 'valid':
        assert result.returncode == 0, result.stdout+'\n'+result.stderr
        assert target.read_bytes() == b'synthetic executable bytes'
    else:
        assert result.returncode != 0
        if scenario == 'existing':
            assert target.read_bytes() == b'preexisting own sentinel'
        else:
            assert not target.exists()
    assert not (tmp_path/'foreign.txt').exists()
    assert not (foreign/'uv.exe').exists()
    assert not list(target.parent.glob('uv.exe.partial-*'))
    if scenario == 'junction':
        os.rmdir(junction)
