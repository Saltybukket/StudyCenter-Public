import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('installer_diagnostics', Path(__file__).resolve().parents[1] / 'scripts/smoke_installer_vm.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def fixture(tmp_path):
    program = tmp_path / 'Program \u00fc space'
    program.mkdir()
    data = tmp_path / 'Data \u00e4 space'
    data.mkdir()
    members = {'StudyCenter-Public.exe': b'synthetic executable', 'components/browser.dat': b'synthetic bundled bytes'}
    for name, content in members.items():
        path = program / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    digest = lambda content: hashlib.sha256(content).hexdigest()
    manifest = {'files': {name: {'size': len(content), 'sha256': digest(content)} for name, content in members.items()}}
    (program / 'BUILD-MANIFEST.json').write_text(json.dumps(manifest), encoding='utf8')
    members['BUILD-MANIFEST.json'] = (program / 'BUILD-MANIFEST.json').read_bytes()
    inventory = ''.join(digest(content) + '|' + name.replace('/', '\\') + '\r\n' for name, content in members.items()).encode()
    (program / 'installation-files.txt').write_bytes(inventory)
    marker = {'application': 'StudyCenter.Public', 'data_directory': str(data), 'inventory_sha256': digest(inventory)}
    (program / 'installation-marker.ini').write_text('[identity]\napplication=StudyCenter.Public\n', encoding='utf8')
    return program, data, marker


def test_diagnostics_observes_matching_inventory_without_writes(tmp_path):
    program, data, marker = fixture(tmp_path)
    before = {p.relative_to(program).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in program.rglob('*') if p.is_file()}
    result = controller.installation_inventory_diagnostics(program, data, str(program), marker_values=marker)
    assert result['marker_application_matches'] and result['marker_data_directory_matches']
    assert result['registry_install_location_matches'] and result['inventory_hash_matches_marker']
    assert result['inventory_delta']['added_count'] == result['inventory_delta']['missing_count'] == result['inventory_delta']['changed_count'] == 0
    assert result['manifest_delta']['added_count'] == 0
    after = {p.relative_to(program).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in program.rglob('*') if p.is_file()}
    assert after == before


def test_diagnostics_identifies_added_changed_and_missing_relative_members(tmp_path):
    program, data, marker = fixture(tmp_path)
    (program / 'StudyCenter-Public.exe').write_bytes(b'synthetic changed bytes')
    (program / 'components/browser.dat').unlink()
    added = program / 'components/DEPENDENCIES_VALIDATED'
    added.write_bytes(b'')
    result = controller.installation_inventory_diagnostics(program, data, str(program), marker_values=marker)
    delta = result['inventory_delta']
    assert (delta['added_count'], delta['missing_count'], delta['changed_count']) == (1, 1, 1)
    assert delta['added'][0]['path'] == 'components/DEPENDENCIES_VALIDATED'
    assert delta['changed'][0]['path'] == 'StudyCenter-Public.exe'
    assert delta['missing'][0]['path'] == 'components/browser.dat'
    assert str(tmp_path) not in json.dumps(result)


def test_marker_registry_and_inventory_tamper_are_separate_booleans(tmp_path):
    program, data, marker = fixture(tmp_path)
    marker.update(application='Synthetic.Other', data_directory=str(tmp_path / 'other-data'), inventory_sha256='0' * 64)
    result = controller.installation_inventory_diagnostics(program, data, str(tmp_path / 'other-program'), marker_values=marker)
    assert not result['marker_application_matches'] and not result['marker_data_directory_matches']
    assert not result['registry_install_location_matches'] and not result['inventory_hash_matches_marker']
    assert str(tmp_path) not in json.dumps(result)


def test_diagnostics_output_is_bounded_and_never_reads_link_target(tmp_path):
    program, data, marker = fixture(tmp_path)
    for n in range(5): (program / ('extra-' + str(n))).write_bytes(b'synthetic additional file')
    outside = tmp_path / 'foreign-fixture'
    outside.mkdir()
    sentinel = outside / 'untouched.txt'
    sentinel.write_bytes(b'synthetic foreign fixture')
    link = program / 'linked'
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip('Own link fixture unavailable')
    before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
    result = controller.installation_inventory_diagnostics(program, data, str(program), marker_values=marker, limit=2)
    assert result['inventory_delta']['added_count'] == 5
    assert len(result['inventory_delta']['added']) == 2
    assert result['blocked_members'] == [{'path': 'linked', 'reason': 'link_or_reparse'}]
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == before
    assert 'untouched.txt' not in json.dumps(result)


def test_diagnostics_does_not_open_inventory_escape(tmp_path):
    program, data, marker = fixture(tmp_path)
    inventory = program / 'installation-files.txt'
    inventory.write_text('0' * 64 + '|..\\foreign-fixture\\private.txt\n')
    result = controller.installation_inventory_diagnostics(program, data, str(program), marker_values=marker)
    assert result['invalid_inventory_entries'] == 1
    assert result['inventory_parse_valid'] is False
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows INI reader required')
def test_native_ini_reader_preserves_unicode_data_identity(tmp_path):
    program, data, marker = fixture(tmp_path)
    text = '[identity]\n' + ''.join(key + '=' + value + '\n' for key, value in marker.items())
    (program / 'installation-marker.ini').write_bytes(text.encode('utf16'))
    result = controller.installation_inventory_diagnostics(program, data, str(program))
    assert result['marker_application_matches'] and result['marker_data_directory_matches']
    assert result['registry_install_location_matches'] and result['inventory_hash_matches_marker']
    assert str(tmp_path) not in json.dumps(result)
