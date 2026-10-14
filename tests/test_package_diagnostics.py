import subprocess

import pytest

from scripts import smoke_release


def test_failed_native_diagnostic_reports_phase_and_redacts_owned_paths(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'synthetic-component-data'
    data.mkdir()
    (data / 'component-phase.txt').write_text('launch full_chromium', encoding='utf-8')
    (data / 'startup-error.txt').write_text('Synthetic failure at ' + str(tmp_path) + '\n' + 'x' * 25000, encoding='utf-8')
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['synthetic-native-command'])
    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        smoke_release.run_component_diagnostic(['synthetic-native-command'], data, {'GITHUB_WORKSPACE': str(tmp_path)})
    output = capsys.readouterr().out
    assert 'launch full_chromium' in output
    assert 'Synthetic failure' in output
    assert str(tmp_path) not in output
    assert '<GITHUB_WORKSPACE>' in output
    assert len(output) < 20500
