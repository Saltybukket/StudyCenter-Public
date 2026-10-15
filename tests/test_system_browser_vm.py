"""Pure child-environment boundary; this does not authorize a VM/browser run."""
import importlib.util
import json
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import subprocess
import socket

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('system_browser_vm_controller', ROOT / 'scripts/smoke_system_browser_vm.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_child_home_xdg_and_temp_are_owned_without_changing_parent(tmp_path):
    parent = {
        'HOME': '/synthetic-runner-home', 'XDG_CACHE_HOME': '/synthetic-runner-cache',
        'XDG_CONFIG_HOME': '/synthetic-runner-config', 'XDG_DATA_HOME': '/synthetic-runner-data',
        'TMPDIR': '/synthetic-runner-tmp', 'TMP': '/synthetic-other-tmp', 'TEMP': '/synthetic-other-temp',
        'LD_LIBRARY_PATH': '/synthetic-bundled-libraries', 'LD_PRELOAD': '/synthetic-preload',
        'CHROME_DEVEL_SANDBOX': '/synthetic-borrowed-helper', 'PYTHONHOME': '/synthetic-python-home',
        'PYTHONPATH': '/synthetic-python-path', 'VIRTUAL_ENV': '/synthetic-venv',
        'PATH': '/synthetic-existing-path',
    }
    original = dict(parent)
    global_environment = dict(os.environ)
    child = controller.isolated_child(tmp_path, parent)
    global_unchanged = dict(os.environ) == global_environment
    assert child is not parent and parent == original and global_unchanged
    names = ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'TMPDIR')
    paths = {Path(child[name]) for name in names}
    assert len(paths) == len(names)
    assert all(path.is_dir() and path.parent == tmp_path and not path.is_symlink() for path in paths)
    if os.name == 'posix':
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in paths)
    assert child['TMP'] == child['TEMP'] == child['TMPDIR']
    assert Path(child['CHROME_LOG_FILE']).parent == tmp_path
    assert all(name not in child for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'CHROME_DEVEL_SANDBOX', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'))
    assert child['PATH'] == original['PATH']


def known_runner_browser():
    """Synthetic metadata, never permission to call require_vm or launch anything."""
    return {'path': '/opt/google/chrome/chrome', 'resolved': '/opt/google/chrome/chrome',
            'regular_file': True, 'ELF': True, 'file_uid': 0, 'file_gid': 0, 'setid': False,
            'executable': True, 'mode': '0o777', 'root_controlled': False,
            'ancestor_metadata': [{'path': path, 'uid': 0, 'directory': True, 'mode': '0o777'}
                                  for path in ['/opt/google/chrome', '/opt/google', '/opt', '/']],
            'package_owner': 'google-chrome-stable: /opt/google/chrome/chrome',
            'package_version': '154.0.8037.97-1',
            'sha256': '6c792041b07547a662e1b17974d1dc34a3db630379b45d9d89ddd7e3e68cc587'}


def test_known_writable_image_allows_only_independent_ui():
    source_allowed, ui_allowed = controller.candidate_eligibility(known_runner_browser(), '20261004.327.1')
    assert source_allowed is False
    assert ui_allowed is True


def current_runner_browser():
    """Metadata observed in UI VM run 38658373042; no browser is launched."""
    entry = known_runner_browser()
    entry.update(package_version='155.0.8059.39-1',
                 sha256='9bfb381296dffe75f3419d07b6cc64525105ed117d1453a4ef3b291cb97d2b58')
    return entry


def test_current_exact_writable_image_allows_only_independent_ui():
    assert controller.candidate_eligibility(current_runner_browser(), '20261012.334.1') == (False, True)


@pytest.mark.parametrize('field,value', [
    ('path', '/synthetic-other/chrome'), ('resolved', '/synthetic-other/chrome'),
    ('regular_file', False), ('ELF', False), ('file_uid', 1001), ('setid', True),
    ('executable', False), ('mode', '0o775'), ('package_owner', 'synthetic-package: /opt/google/chrome/chrome'),
    ('package_version', '154.0.8037.98-1'), ('sha256', '0' * 64),
    ('ancestor_metadata', [{'path': '/opt', 'uid': 1001, 'mode': '0o777'}]),
])
@pytest.mark.parametrize('fixture,image', [(known_runner_browser, '20261004.327.1'),
                                          (current_runner_browser, '20261012.334.1')])
def test_writable_ui_exception_never_accepts_other_candidate(field, value, fixture, image):
    entry = fixture()
    entry[field] = value
    assert controller.candidate_eligibility(entry, image) == (False, False)


def test_other_image_does_not_inherit_writable_ui_exception():
    assert controller.candidate_eligibility(known_runner_browser(), '20261005.1') == (False, False)


@pytest.mark.parametrize('fixture,image', [(known_runner_browser, '20261012.334.1'),
                                          (current_runner_browser, '20261004.327.1'),
                                          (current_runner_browser, '20261013.334.1')])
def test_image_records_never_mix_or_admit_a_future_image(fixture, image):
    assert controller.candidate_eligibility(fixture(), image) == (False, False)


@pytest.mark.parametrize('field,value', [
    ('package_version', '154.0.8037.97-1'),
    ('sha256', '6c792041b07547a662e1b17974d1dc34a3db630379b45d9d89ddd7e3e68cc587'),
])
def test_current_image_never_accepts_an_old_package_or_old_elf(field, value):
    entry = current_runner_browser()
    entry[field] = value
    assert controller.candidate_eligibility(entry, '20261012.334.1') == (False, False)


def test_normal_strict_browser_keeps_both_lanes_eligible():
    entry = known_runner_browser()
    entry.update(root_controlled=True, mode='0o755')
    assert controller.candidate_eligibility(entry, '20261004.327.1') == (True, True)


@pytest.mark.parametrize('policy', [
    {}, {'present': False, 'root_controlled': True, 'loaded_chrome_profiles': ['chrome (unconfined)']},
    {'present': True, 'root_controlled': False, 'loaded_chrome_profiles': ['chrome (unconfined)']},
    {'present': True, 'root_controlled': True, 'loaded_chrome_profiles': []},
])
def test_unknown_confinement_never_allows_either_probe(policy):
    assert controller.confinement_eligible({'existing_chrome_policy': policy}) is False


def test_present_unchanged_loaded_policy_is_only_a_prelaunch_gate():
    assert controller.confinement_eligible({'existing_chrome_policy': {
        'present': True, 'root_controlled': True, 'loaded_chrome_profiles': ['chrome (unconfined)'],
    }}) is True


@pytest.mark.parametrize('field,value', [('AppArmor_label', 'unconfined'), ('Seccomp', 0),
                                       ('NoNewPrivs', 0), ('different_user_namespace', False)])
def test_each_actual_renderer_must_have_label_and_sandbox_evidence(field, value):
    good = {'AppArmor_label': 'chrome (unconfined)', 'Seccomp': 2, 'NoNewPrivs': 1,
            'different_user_namespace': True}
    bad = dict(good)
    bad[field] = value
    assert controller.renderer_sandbox_eligible([good]) is True
    assert controller.renderer_sandbox_eligible([good, bad]) is False
    assert controller.renderer_sandbox_eligible([]) is False


def test_late_verification_failure_never_keeps_ui_pass_or_opens_blocked_source():
    result = {'UI_lane': {'status': 'PASS', 'overview_rendered': True},
              'source_lane': {'status': 'BLOCKED', 'reason': 'Strict source ownership failed'}}
    controller.invalidate_passes(result, 'Actual system metadata changed')
    assert result['UI_lane']['status'] == 'OPEN'
    assert result['UI_lane']['overview_rendered'] is True
    assert result['source_lane'] == {'status': 'BLOCKED', 'reason': 'Strict source ownership failed'}


def test_dispatch_failure_diagnostics_preserve_code_and_redact_own_paths(tmp_path):
    base = 'http://127.0.0.1:41721'
    output = ('dispatcher failed ' + str(tmp_path) + '/own-wrapper ' + base + '/overview password=synthetic-value').encode()
    details = controller.collect_dispatch_diagnostics(tmp_path, base, 4, output, b'synthetic error')
    assert details['returncode'] == 4 and details['timed_out'] is False
    assert details['wrapper_pid_receipt']['present'] is False
    assert 'dispatcher failed' in details['stdout_tail']
    assert str(tmp_path) not in details['stdout_tail'] and base not in details['stdout_tail']
    assert 'synthetic-value' not in details['stdout_tail']
    assert details['stderr_tail'] == 'synthetic error'


def test_dispatch_own_browser_log_tail_is_bounded_and_empty_receipt_is_visible(tmp_path):
    (tmp_path / 'own-ui-browser.json').write_bytes(b'')
    (tmp_path / 'own-ui-browser.log').write_text('x' * 30000 + '\nOWN_CHROME_STARTUP_ERROR ' + str(tmp_path))
    details = controller.collect_dispatch_diagnostics(tmp_path, 'http://127.0.0.1:41721', 1)
    assert details['wrapper_pid_receipt']['present'] is True
    assert details['wrapper_pid_receipt']['regular_file'] is True
    assert details['wrapper_pid_receipt']['size'] == 0
    tail = details['own_browser_output']['tail']
    assert len(tail) <= 2048 and 'OWN_CHROME_STARTUP_ERROR' in tail and str(tmp_path) not in tail
    assert details['own_browser_output']['truncated'] is True
    assert details['own_chrome_log']['present'] is False


def test_dispatch_timeout_partial_binary_output_is_json_safe(tmp_path):
    details = controller.collect_dispatch_diagnostics(tmp_path, 'http://127.0.0.1:41721', stdout=b'partial\xff', timed_out=True)
    assert details['returncode'] is None and details['timed_out'] is True
    assert details['stdout_tail'].startswith('partial')


def test_dispatch_diagnostics_never_follow_linked_log(tmp_path):
    foreign = tmp_path / 'own-foreign-sentinel'
    foreign.write_bytes(b'SYNTHETIC_FOREIGN_SENTINEL_DO_NOT_READ')
    link = tmp_path / 'own-ui-browser.log'
    try:
        link.symlink_to(foreign)
    except OSError:
        pytest.skip('Own synthetic link unavailable')
    details = controller.collect_dispatch_diagnostics(tmp_path, 'http://127.0.0.1:41721', 1)
    assert details['own_browser_output']['read_error_type'] == 'ValueError'
    assert 'SYNTHETIC_FOREIGN_SENTINEL_DO_NOT_READ' not in str(details)
    assert foreign.read_bytes() == b'SYNTHETIC_FOREIGN_SENTINEL_DO_NOT_READ'


def test_dispatch_diagnostics_redact_entire_authorization_and_bearer_lines(tmp_path):
    value = (b'first useful line\nAuthorization: Bearer SYNTHETIC_TWO_PART_VALUE\n'
             b'Bearer SYNTHETIC_STANDALONE_VALUE\n"Authorization": "Basic SYNTHETIC_BASIC_VALUE"\nlast useful line')
    details = controller.collect_dispatch_diagnostics(tmp_path, 'http://127.0.0.1:41721', 4, stderr=value)
    assert details['returncode'] == 4
    assert all(marker not in details['stderr_tail'] for marker in
               ('SYNTHETIC_TWO_PART_VALUE', 'SYNTHETIC_STANDALONE_VALUE', 'SYNTHETIC_BASIC_VALUE'))
    assert details['stderr_tail'].startswith('first useful line\n')
    assert details['stderr_tail'].endswith('\nlast useful line')


@pytest.mark.skipif(os.name != 'posix', reason='Actual Ubuntu-style /bin/sh dispatch boundary')
def test_own_xdg_exec_resolves_owned_wrapper_with_legacy_ubuntu_parser(tmp_path):
    fixture = tmp_path / 'own fixture with space'
    fixture.mkdir()
    parent = {'PATH': '/usr/bin:/bin', 'HOME': str(fixture), 'LC_ALL': 'C'}
    original = dict(parent)
    child, _ = controller.install_own_xdg_dispatch(fixture, fixture / 'own-profile',
                                                  'http://127.0.0.1:41721', parent)
    desktop = fixture / 'own-xdg-data/applications/StudyCenter-Public-Synthetic.desktop'
    entry = next(line.removeprefix('Exec=') for line in desktop.read_text().splitlines() if line.startswith('Exec='))
    # Noble xdg-utils 1.1.3-4.1ubuntu3 first_word preserves quote characters.
    # Resolve only; never execute the generated browser wrapper.
    script = 'read first rest; command_exec=`which $first 2>/dev/null`; [ "$command_exec" = "$EXPECTED_WRAPPER" ]'
    result = subprocess.run(['/bin/sh', '-c', script], input=entry+'\n', text=True,
                            env=dict(child, EXPECTED_WRAPPER=str(fixture/'own-system-browser-wrapper')),
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert parent == original


@pytest.mark.skipif(not hasattr(socket, 'AF_UNIX') or os.name != 'posix', reason='Actual Linux Unix-socket boundary')
def test_short_child_temp_binds_chrome_singleton_socket_and_closes_last(tmp_path):
    fixture = tmp_path / ('own-long-fixture-' + 'x' * 80)
    fixture.mkdir()
    parent = {'HOME': '/synthetic-parent-home', 'TMPDIR': '/synthetic-parent-temp',
              'TMP': '/synthetic-parent-tmp', 'TEMP': '/synthetic-parent-other', 'PATH': '/usr/bin:/bin'}
    original = dict(parent)
    global_environment = dict(os.environ)
    events = []
    with ExitStack() as stack:
        child = controller.isolated_child(fixture, parent, temporary_stack=stack)
        temporary = Path(child['TMPDIR'])
        chrome = temporary / 'com.google.Chrome.iaHx00'
        chrome.mkdir()
        endpoint = chrome / 'SingletonSocket'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.bind(str(endpoint))
        assert len(os.fsencode(endpoint)) < 108
        assert temporary.parent == Path('/tmp') and not temporary.is_symlink()
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o700
        assert temporary.stat().st_uid == os.geteuid()
        assert child['TMP'] == child['TEMP'] == child['TMPDIR']
        assert all(Path(child[key]).parent == fixture for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'))
        for name in ('backend', 'xvfb', 'browser'):
            stack.callback(lambda label=name: events.append((label, temporary.is_dir())))
    assert events == [('browser', True), ('xvfb', True), ('backend', True)]
    assert not temporary.exists()
    assert parent == original and dict(os.environ) == global_environment


def test_late_sandbox_failure_retains_only_observed_render_proofs_as_open():
    diagnostics = {'partial_render_evidence': {
        'status': 'PASS', 'normal_dispatch': True, 'overview_rendered': True,
        'PDF_reader_DOM_and_image': True, 'UI_screenshot_sha256': 'a' * 64,
        'reader_screenshot_sha256': 'b' * 64, 'untrusted_extra': 'SYNTHETIC_PRIVATE_VALUE'},
        'sandbox_diagnostics': {'native_processes': {'complete': False}}}
    result = controller.ui_failure_result('Actual sandbox evidence is missing.', diagnostics)
    assert result['status'] == 'OPEN'
    assert result['overview_rendered'] is True and result['PDF_reader_DOM_and_image'] is True
    assert result['UI_screenshot_sha256'] == 'a' * 64 and result['reader_screenshot_sha256'] == 'b' * 64
    assert 'untrusted_extra' not in result and 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(result)


def test_sandbox_page_diagnostics_expose_case_without_relaxing_gate_or_leaking_secrets():
    body = ('Namespace sandbox\tYes\nSeccomp-BPF sandbox\tYes\n'
            'Authorization: Bearer SYNTHETIC_AUTH_VALUE\n"token": "SYNTHETIC_TOKEN_VALUE"\n'
            'https://synthetic.invalid/private /tmp/synthetic-private/path C:\\synthetic\\private\\path\n')
    result = controller.sandbox_page_diagnostics(body)
    assert result['case_sensitive_gate'] == {'namespace': False, 'seccomp': True}
    assert 'Namespace sandbox' in json.dumps(result)
    assert all(value not in json.dumps(result) for value in ('SYNTHETIC_AUTH_VALUE', 'SYNTHETIC_TOKEN_VALUE',
        'https://synthetic.invalid', '/tmp/synthetic-private', 'C:\\synthetic'))
    large = controller.sandbox_page_diagnostics(body + 'x' * 40000)
    assert len(large['body_excerpt']) <= 4096 and len(large['observed_lines']) <= 32


def test_missing_renderer_native_facts_never_confirm_sandbox():
    assert controller.renderer_sandbox_eligible([{'pid': 1, 'Seccomp': 2}]) is False


def test_sandbox_diagnostics_never_print_quoted_multiline_token_values():
    body = 'Namespace Sandbox\tYes\n"token":\n  "SYNTHETIC_MULTILINE_VALUE"\nSeccomp-BPF sandbox\tYes'
    result = controller.sandbox_page_diagnostics(body)
    assert result['case_sensitive_gate'] == {'namespace': True, 'seccomp': True}
    assert 'SYNTHETIC_MULTILINE_VALUE' not in json.dumps(result)


def test_open_ui_failure_never_projects_an_invalid_hash_or_raw_error_secret():
    result = controller.ui_failure_result('Authorization: Bearer SYNTHETIC_ERROR_VALUE', {
        'partial_render_evidence': {'UI_screenshot_sha256': 'https://synthetic.invalid/path',
                                    'reader_screenshot_sha256': 'b' * 64}})
    assert result['status'] == 'OPEN' and result['reader_screenshot_sha256'] == 'b' * 64
    assert 'UI_screenshot_sha256' not in result
    assert 'SYNTHETIC_ERROR_VALUE' not in json.dumps(result)


SANDBOX_REQUIRED_ROWS=['Layer 1 Sandbox\tNamespace','PID namespaces\tYes',
                       'Network namespaces\tYes','Seccomp-BPF sandbox\tYes']
SANDBOX_BODY='Sandbox Status\n'+'\n'.join(SANDBOX_REQUIRED_ROWS)+'\nSeccomp-BPF sandbox supports TSYNC\tYes\nPtrace Protection with Yama LSM (Broker)\tYes\nPtrace Protection with Yama LSM (Non-broker)\tNo\n\nYou are adequately sandboxed.'


def test_current_desktop_sandbox_rows_accept_legitimate_nonbroker_yama_no():
    assert controller.sandbox_rows_verified(SANDBOX_BODY) is True
    assert controller.sandbox_page_diagnostics(SANDBOX_BODY)['verified_desktop_rows'] is True


@pytest.mark.parametrize('row',SANDBOX_REQUIRED_ROWS)
@pytest.mark.parametrize('mutation',['missing','duplicate','no','unknown','malformed'])
def test_current_sandbox_required_rows_fail_closed(row,mutation):
    if mutation=='missing':bad=SANDBOX_BODY.replace(row+'\n','')
    elif mutation=='duplicate':bad=SANDBOX_BODY.replace(row,row+'\n'+row)
    elif mutation in ('no','unknown'):bad=SANDBOX_BODY.replace(row,row.split('\t')[0]+'\t'+mutation.title())
    else:bad=SANDBOX_BODY.replace(row,row.replace('\t',' '))
    assert controller.sandbox_rows_verified(bad) is False


@pytest.mark.parametrize('extra',['Unknown sandbox row\tYes','Layer 1 Sandbox\tSUID',
    'Layer 1 Sandbox\tNone','PID namespaces\tNo','Network namespaces\tUnknown',
    'Seccomp-BPF sandbox\tYes\textra','Seccomp-BPF sandbox supports TSYNC\tUnknown',
    'Seccomp-BPF sandbox supports TSYNC\tYes','fake positive summary'])
def test_current_sandbox_extra_rows_never_supply_or_conflict_with_proof(extra):
    bad=SANDBOX_BODY.replace('\n\nYou are adequately sandboxed.','\n'+extra+'\n\nYou are adequately sandboxed.')
    assert controller.sandbox_rows_verified(bad) is False


def test_legacy_sandbox_phrase_never_substitutes_for_complete_current_rows():
    assert controller.sandbox_rows_verified('Namespace Sandbox\tYes\nSeccomp-BPF sandbox\tYes') is False


@pytest.mark.parametrize('bad',[SANDBOX_BODY.lower(),SANDBOX_BODY.replace('Namespace','Namespace '),
    SANDBOX_BODY.replace('Sandbox Status','Sandbox Status\nSandbox Status'),
    SANDBOX_BODY.replace('You are adequately sandboxed.','You are NOT adequately sandboxed.')])
def test_current_sandbox_changed_case_values_or_summary_fail_closed(bad):
    assert controller.sandbox_rows_verified(bad) is False
