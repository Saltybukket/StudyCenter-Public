"""Existing system Chrome, own XDG UI and independent source probe in a disposable VM."""
import argparse
from contextlib import ExitStack
import hashlib
import importlib.metadata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_CHROME = Path('/opt/google/chrome/chrome')
PROFILE_POLICY = Path('/etc/apparmor.d/chrome')
NAMESPACE_VALUES = (
    'kernel/apparmor_restrict_unprivileged_userns',
    'kernel/unprivileged_userns_clone', 'user/max_user_namespaces',
)


def require_vm():
    if sys.platform != 'linux' or os.geteuid() == 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('This probe requires a non-root, disposable GitHub-hosted Linux VM.')
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="24\.04"$', release, re.M):
        raise RuntimeError('The probe requires actual Ubuntu 24.04.')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    if not temporary.is_dir():
        raise RuntimeError('Own runner temporary directory unavailable.')
    return temporary


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def own_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    return path


def cleaned_child(environment):
    child = dict(environment)
    for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'CHROME_DEVEL_SANDBOX', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
        child.pop(name, None)
    return child


def isolated_child(fixture, environment):
    child = cleaned_child(environment)
    for name, suffix in (('HOME', 'own-home'), ('XDG_CACHE_HOME', 'own-xdg-cache'),
                         ('XDG_CONFIG_HOME', 'own-base-config'), ('XDG_DATA_HOME', 'own-base-data'),
                         ('TMPDIR', 'own-temp')):
        child[name] = str(own_directory(fixture / suffix))
    child['TMP'] = child['TEMP'] = child['TMPDIR']
    child['CHROME_LOG_FILE'] = str(fixture / 'chrome.log')
    return child


def read_audit(path, link=False):
    """Only OS confinement or already-attested own browser process metadata."""
    try:
        return os.readlink(path) if link else Path(path).read_text()
    except PermissionError:
        command = ['sudo', '-n', 'readlink' if link else 'cat', str(path)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if completed.returncode:
            raise RuntimeError('Read-only confinement audit unavailable')
        return completed.stdout.strip()


def confinement_snapshot():
    values = {}
    for name in NAMESPACE_VALUES:
        path = Path('/proc/sys') / name
        values[name] = path.read_text().strip() if path.exists() else None
    policy = {'present': PROFILE_POLICY.is_file(), 'sha256': digest(PROFILE_POLICY) if PROFILE_POLICY.is_file() else None}
    if PROFILE_POLICY.is_file():
        metadata = PROFILE_POLICY.stat()
        policy['root_controlled'] = metadata.st_uid == 0 and not metadata.st_mode & 0o022
    loaded = read_audit('/sys/kernel/security/apparmor/profiles')
    policy['loaded_chrome_profiles'] = sorted(line for line in loaded.splitlines() if re.match(r'^chrome(?:\s|/)', line))
    return {'namespace_restrictions': values, 'existing_chrome_policy': policy}


def inventory():
    result = []
    for name in ('/opt/google/chrome/chrome', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium'):
        path = Path(name)
        entry = {'path': name, 'present': path.exists()}
        if path.exists():
            actual = path.resolve(strict=True)
            info = actual.stat()
            if not stat.S_ISREG(info.st_mode):
                entry.update({'resolved': str(actual), 'ELF': False, 'regular_file': False})
                result.append(entry)
                continue
            with actual.open('rb') as stream:
                elf = stream.read(4) == b'\x7fELF'
            ancestors = (actual, *actual.parents)
            controlled = all(candidate.stat().st_uid == 0 and not candidate.stat().st_mode & 0o022 for candidate in ancestors)
            controlled = controlled and not info.st_mode & (stat.S_ISUID | stat.S_ISGID) and os.access(actual, os.X_OK)
            owner = subprocess.run(['dpkg-query', '-S', str(actual)], capture_output=True, text=True, timeout=10)
            package_version = None
            if owner.returncode == 0:
                package = owner.stdout.splitlines()[0].split(': ', 1)[0]
                if re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?', package):
                    version = subprocess.run(['dpkg-query', '-W', '-f=${Version}', package], capture_output=True, text=True, timeout=10)
                    if version.returncode == 0:
                        package_version = version.stdout.strip()
            entry.update({'resolved': str(actual), 'ELF': elf, 'regular_file': True, 'mode': oct(info.st_mode & 0o7777), 'root_controlled': controlled,
                          'sha256': digest(actual), 'package_version': package_version,
                          'package_owner': owner.stdout.strip()[:300] if owner.returncode == 0 else None})
        result.append(entry)
    return result


def wait_for(predicate, process=None, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        if process is not None and process.poll() is not None:
            raise RuntimeError('Own process exited before readiness.')
        time.sleep(.15)
    raise RuntimeError('Own process readiness timed out.')


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def start_xvfb(fixture, environment, stack):
    executable, xauth = shutil.which('Xvfb'), shutil.which('xauth')
    if not executable or not xauth:
        return None
    for _ in range(20):
        number = 1000 + secrets.randbelow(50000)
        if not Path(f'/tmp/.X{number}-lock').exists() and not Path(f'/tmp/.X11-unix/X{number}').exists():
            break
    else:
        raise RuntimeError('No free own virtual display found.')
    authority = fixture / 'own-Xauthority'
    subprocess.run([xauth, '-f', str(authority), 'add', f':{number}', 'MIT-MAGIC-COOKIE-1', secrets.token_hex(16)],
                   check=True, capture_output=True, timeout=10, env=environment)
    authority.chmod(0o600)
    output = stack.enter_context((fixture / 'xvfb.log').open('wb'))
    process = subprocess.Popen([executable, f':{number}', '-screen', '0', '1440x1000x24', '-nolisten', 'tcp', '-auth', str(authority)],
                               stdout=output, stderr=subprocess.STDOUT, env=environment)
    stack.callback(stop_process, process)
    wait_for(lambda: Path(f'/tmp/.X11-unix/X{number}').exists(), process)
    return dict(environment, DISPLAY=f':{number}', XAUTHORITY=str(authority))


def backend_start(fixture, environment, executable, stack):
    data = fixture / 'public-data'
    command = [str(executable)] if executable else [sys.executable, '-m', 'studycenter_public']
    child = dict(environment)
    if not executable:
        child['PYTHONPATH'] = str(ROOT / 'src')
    output = stack.enter_context((fixture / 'backend.log').open('wb'))
    process = subprocess.Popen(command + ['--data-dir', str(data), '--port', '0', '--no-browser'], cwd=fixture,
                               env=child, stdout=output, stderr=subprocess.STDOUT)
    def shutdown():
        if process.poll() is None and (data / 'runtime.json').exists():
            (data / 'shutdown.request').write_text('stop')
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                stop_process(process)
        else:
            stop_process(process)
    stack.callback(shutdown)
    endpoint = wait_for(lambda: json.loads((data / 'runtime.json').read_text()) if (data / 'runtime.json').is_file() else None, process)
    base = endpoint['url']
    def healthy():
        try:
            with urllib.request.urlopen(base + '/api/health', timeout=2) as response:
                return json.load(response).get('application') == 'StudyCenter.Public'
        except OSError:
            return False
    wait_for(healthy, process)
    return base, process, data


def install_own_xdg_dispatch(fixture, profile, base, environment):
    wrapper = fixture / 'own-system-browser-wrapper'
    pid_receipt = fixture / 'own-ui-browser.json'
    log = fixture / 'own-ui-browser.log'
    arguments = [str(SYSTEM_CHROME), '--user-data-dir=' + str(profile), '--remote-debugging-address=127.0.0.1',
                 '--remote-debugging-port=0', '--enable-automation', '--no-first-run', '--no-default-browser-check',
                 '--disable-background-networking', '--disable-component-update', '--disable-sync', '--password-store=basic',
                 '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']
    wrapper.write_text('#!' + sys.executable + '\n' +
        'import json,os,subprocess,sys\n' +
        f'if len(sys.argv)!=2 or not sys.argv[1].startswith({(base + "/")!r}): raise SystemExit(2)\n' +
        f'fd=os.open({str(pid_receipt)!r},os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n' +
        f'log=open({str(log)!r},"ab")\n' +
        f'process=subprocess.Popen({arguments!r}+[sys.argv[1]],stdout=log,stderr=log,start_new_session=True)\n' +
        'with os.fdopen(fd,"w") as stream: json.dump({"pid":process.pid},stream)\n', encoding='utf-8')
    wrapper.chmod(0o700)
    config = own_directory(fixture / 'own-xdg-config')
    data = own_directory(fixture / 'own-xdg-data')
    applications = own_directory(data / 'applications')
    desktop = 'StudyCenter-Public-Synthetic.desktop'
    (applications / desktop).write_text('[Desktop Entry]\nType=Application\nName=StudyCenter Public own test browser\n' +
        'Exec="' + str(wrapper).replace('\\', '\\\\').replace('"', '\\"') + '" %u\nTerminal=false\n' +
        'MimeType=x-scheme-handler/http;x-scheme-handler/https;text/html;\n', encoding='utf-8')
    (config / 'mimeapps.list').write_text('[Default Applications]\nx-scheme-handler/http=' + desktop + ';\n' +
        'x-scheme-handler/https=' + desktop + ';\ntext/html=' + desktop + ';\n', encoding='utf-8')
    return dict(environment, XDG_CONFIG_HOME=str(config), XDG_DATA_HOME=str(data), BROWSER=str(wrapper)), pid_receipt


def own_browser_process(pid, profile):
    if type(pid) is not int or pid < 1:
        raise RuntimeError('Invalid own browser PID.')
    command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    if os.fsencode('--user-data-dir=' + str(profile)) not in command:
        raise RuntimeError('Browser PID does not attest this own UUID profile.')
    if Path(f'/proc/{pid}/exe').resolve() != SYSTEM_CHROME.resolve():
        raise RuntimeError('Own browser executable differs from the audited system installation.')


def stop_own_browser(pid_receipt, profile):
    if not pid_receipt.is_file():
        return
    pid = json.loads(pid_receipt.read_text()).get('pid')
    try:
        own_browser_process(pid, profile)
        os.killpg(pid, signal.SIGTERM)
    except FileNotFoundError:
        pass


def sandbox_evidence(context, profile):
    browser = context.browser
    session = browser.new_browser_cdp_session()
    command = session.send('Browser.getBrowserCommandLine')['arguments']
    if '--user-data-dir=' + str(profile) not in command or any(flag in command for flag in ('--no-sandbox', '--disable-setuid-sandbox', '--disable-namespace-sandbox')):
        raise RuntimeError('Actual browser command line does not preserve the required sandbox/profile.')
    version = context.new_page()
    version.goto('chrome://version')
    executable = version.locator('#executable_path').inner_text().strip()
    actual_profile = Path(version.locator('#profile_path').inner_text().strip())
    if Path(executable).resolve() != SYSTEM_CHROME.resolve() or not actual_profile.is_relative_to(profile):
        raise RuntimeError('Actual Chrome executable/profile differs from the own selected installation.')
    version.goto('chrome://sandbox')
    text = version.locator('body').inner_text()
    namespace = bool(re.search(r'Namespace Sandbox\s+Yes', text))
    seccomp = bool(re.search(r'Seccomp-BPF sandbox\s+Yes', text))
    if not namespace or not seccomp:
        raise RuntimeError('Actual Chromium namespace/seccomp sandbox is not confirmed.')
    own_namespace = os.readlink('/proc/self/ns/user')
    own_pid_namespace = os.readlink('/proc/self/ns/pid')
    processes = session.send('SystemInfo.getProcessInfo')['processInfo']
    browser_pid = int(next(info['id'] for info in processes if info['type'] == 'browser'))
    own_browser_process(browser_pid, profile)
    browser_label = read_audit(f'/proc/{browser_pid}/attr/current').strip()
    if not (browser_label.startswith('chrome ') or browser_label.startswith('chrome//')):
        raise RuntimeError('Actual own Chrome process is not using the attested existing Chrome profile.')
    renderers = []
    for info in processes:
        if info['type'] != 'renderer':
            continue
        pid = int(info['id'])
        status = read_audit(f'/proc/{pid}/status')
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        renderer = {'pid': pid, 'Seccomp': int(fields['Seccomp'].strip()), 'NoNewPrivs': int(fields['NoNewPrivs'].strip()),
                    'different_user_namespace': read_audit(f'/proc/{pid}/ns/user', link=True) != own_namespace,
                    'different_pid_namespace': read_audit(f'/proc/{pid}/ns/pid', link=True) != own_pid_namespace}
        renderers.append(renderer)
    if not any(value['Seccomp'] == 2 and value['NoNewPrivs'] == 1 and value['different_user_namespace'] for value in renderers):
        raise RuntimeError('Own renderer sandbox process evidence is incomplete.')
    version.close()
    return {'version': browser.version, 'executable': executable, 'own_profile': True, 'namespace_sandbox': namespace,
            'seccomp_sandbox': seccomp, 'existing_browser_AppArmor_label': browser_label,
            'renderer_processes': renderers, 'chromium_sandbox_bypass_flags': False}


def ui_probe(playwright, fixture, environment, base, stack):
    profile = own_directory(fixture / ('ui-profile-' + uuid4().hex))
    child, pid_receipt = install_own_xdg_dispatch(fixture, profile, base, environment)
    stack.callback(stop_own_browser, pid_receipt, profile)
    dispatch = subprocess.run(['xdg-open', base + '/overview'], env=child, capture_output=True, timeout=30)
    if dispatch.returncode:
        raise RuntimeError('Own xdg-open browser dispatch failed.')
    acknowledgement = wait_for(lambda: json.loads(pid_receipt.read_text()) if pid_receipt.is_file() and pid_receipt.stat().st_size else None)
    own_browser_process(acknowledgement['pid'], profile)
    active = profile / 'DevToolsActivePort'
    lines = wait_for(lambda: active.read_text().splitlines() if active.is_file() else None)
    if active.is_symlink() or not lines[0].isdigit() or not 1 <= int(lines[0]) <= 65535:
        raise RuntimeError('Own debug endpoint receipt invalid.')
    browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + lines[0])
    stack.callback(browser.close)
    context = browser.contexts[0]
    page = next((page for page in context.pages if page.url.startswith(base)), None) or context.new_page()
    page.goto(base + '/overview')
    page.get_by_role('heading', name='Übersicht', exact=True).wait_for()
    screen = fixture / 'own-xdg-ui.png'
    page.screenshot(path=screen, full_page=True)
    session = context.request.get(base + '/api/session').json()
    import pymupdf
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), 'Synthetic Public PDF reader sentinel')
    content = pdf.tobytes(); pdf.close()
    imported = context.request.post(base + '/api/documents/import', headers={'Origin': base, 'X-CSRF-Token': session['csrf']},
        multipart={'file': {'name': 'Synthetic Public.pdf', 'mimeType': 'application/pdf', 'buffer': content}})
    if not imported.ok:
        raise RuntimeError('Own synthetic PDF import failed.')
    page.goto(base + '/documents/' + str(imported.json()['id']))
    page.get_by_text('Synthetic Public PDF reader sentinel', exact=False).first.wait_for()
    page.locator('img.pdf-page').first.wait_for(state='visible')
    page.wait_for_function("() => { const image = document.querySelector('img.pdf-page'); return image && image.complete && image.naturalWidth > 0; }", timeout=30000)
    reader = fixture / 'own-pdf-reader.png'; page.screenshot(path=reader, full_page=True)
    evidence = sandbox_evidence(context, profile)
    return {'status': 'PASS', 'scope': 'own xdg-open dispatch, Xvfb-headed UI; no real desktop acceptance',
            'normal_dispatch': True, 'overview_rendered': True, 'PDF_reader_DOM_and_image': True,
            'UI_screenshot_sha256': digest(screen), 'reader_screenshot_sha256': digest(reader), 'sandbox': evidence}


def source_probe(playwright, fixture, environment, stack):
    class OwnSource(BaseHTTPRequestHandler):
        def do_GET(self):
            content = b'<html><title>Own synthetic source</title><body>StudyCenter Public source sentinel</body></html>'
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.send_header('Content-Length', str(len(content))); self.end_headers(); self.wfile.write(content)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), OwnSource)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.server_close); stack.callback(server.shutdown)
    profile = own_directory(fixture / ('source-profile-' + uuid4().hex))
    context = playwright.chromium.launch_persistent_context(str(profile), executable_path=str(SYSTEM_CHROME), chromium_sandbox=True,
        headless=True, env=environment, args=['--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
    stack.callback(context.close)
    page = context.new_page(); page.goto('http://127.0.0.1:' + str(server.server_port))
    if page.title() != 'Own synthetic source' or 'StudyCenter Public source sentinel' not in page.locator('body').inner_text():
        raise RuntimeError('Own synthetic source did not render.')
    screen = fixture / 'own-source.png'; page.screenshot(path=screen)
    evidence = sandbox_evidence(context, profile)
    return {'status': 'PASS', 'scope': 'existing Chrome headless, own synthetic loopback source', 'title_and_text': True,
            'screenshot_sha256': digest(screen), 'sandbox': evidence, 'real_TU_or_account': False}


def run(executable=None, expected_source=None, expected_executable_sha256=None):
    temporary = require_vm()
    fixture = own_directory(temporary / ('StudyCenter-Public-system-browser-' + uuid4().hex))
    result = {'scope': 'separate disposable VM system-browser lane', 'actual_desktop_acceptance': False,
              'workflow_exit_is_transport_only': True, 'controller_source_git_sha': os.environ.get('GITHUB_SHA'),
              'UI_backend': 'exact frozen package' if executable else 'source Python', 'source_ui': not bool(executable),
              'UI_lane': {'status': 'NOT_RUN'}, 'source_lane': {'status': 'NOT_RUN'}, 'source_git_sha': os.environ.get('GITHUB_SHA'),
              'platform': sys.platform, 'kernel': os.uname().release, 'uid': os.geteuid(), 'runner_image': os.environ.get('ImageVersion'),
              'playwright_version': importlib.metadata.version('playwright')}
    def error_message(error):
        text = type(error).__name__ + ': ' + str(error)
        for value in (str(fixture), str(ROOT), os.environ.get('HOME', '')):
            if value:
                text = text.replace(value, '<OWN_VM_PATH>')
        return text[:1600]
    before = None
    try:
        result['existing_browser_inventory'] = inventory()
        result['prerequisites'] = {name: {'status': 'AVAILABLE' if shutil.which(name) else 'BLOCKED'} for name in ('Xvfb', 'xauth', 'xdg-open')}
        selected = next((entry for entry in result['existing_browser_inventory'] if entry['path'] == str(SYSTEM_CHROME) and entry.get('ELF') and entry.get('root_controlled') and entry.get('package_owner')), None)
        if not selected:
            result['prerequisites']['regular_system_Chrome'] = {'status': 'BLOCKED'}
            result['UI_lane'] = result['source_lane'] = {'status': 'BLOCKED', 'reason': 'No attested regular system Chrome; no browser was installed or substituted.'}
            return result
        result['selected_system_browser_sha256'] = selected['sha256']
        result['prerequisites']['regular_system_Chrome'] = {'status': 'AVAILABLE'}
        before = confinement_snapshot(); result['confinement_before'] = before
        policy = before['existing_chrome_policy']
        if not policy['present'] or not policy.get('root_controlled') or not policy['loaded_chrome_profiles']:
            result['UI_lane'] = result['source_lane'] = {'status': 'BLOCKED', 'reason': 'Existing loaded regular Chrome AppArmor policy is not attested; no policy was installed or changed.'}
            return result
        if executable:
            executable = Path(executable).resolve(strict=True)
            if not executable.is_relative_to(temporary) or not re.fullmatch(r'[0-9a-f]{40}', expected_source or '') or not re.fullmatch(r'[0-9a-f]{64}', expected_executable_sha256 or ''):
                raise RuntimeError('Native probe requires an own runner-temporary package and explicit source/SHA bindings.')
            if digest(executable) != expected_executable_sha256:
                raise RuntimeError('Native executable checksum mismatch.')
            manifest = json.loads((executable.parent / 'BUILD-MANIFEST.json').read_text())
            if manifest.get('source_git_sha') != expected_source:
                raise RuntimeError('Native manifest source mismatch.')
            for name, entry in manifest['files'].items():
                member = executable.parent / name
                if not member.resolve().is_relative_to(executable.parent) or member.stat().st_size != entry['size'] or digest(member) != entry['sha256']:
                    raise RuntimeError('Native package member mismatch.')
            result['source_git_sha'] = expected_source; result['native_executable_sha256'] = expected_executable_sha256
        environment = isolated_child(fixture, os.environ)
        backend = None
        with ExitStack() as stack:
            from playwright.sync_api import sync_playwright
            playwright = stack.enter_context(sync_playwright())
            try:
                result['source_lane'] = source_probe(playwright, fixture, environment, stack)
            except Exception as error:
                result['source_lane'] = {'status': 'OPEN', 'error': error_message(error)}
            display_environment = start_xvfb(fixture, environment, stack)
            if not display_environment or not shutil.which('xdg-open'):
                result['UI_lane'] = {'status': 'BLOCKED', 'reason': 'No preinstalled Xvfb/xauth/xdg-open; no installation performed.'}
            else:
                try:
                    backend = backend_start(fixture, display_environment, executable, stack)
                    base = backend[0]
                    result['UI_lane'] = ui_probe(playwright, fixture, display_environment, base, stack)
                except Exception as error:
                    result['UI_lane'] = {'status': 'OPEN', 'error': error_message(error)}
        if backend is not None:
            result['backend_cooperative_shutdown'] = backend[1].returncode == 0 and not (backend[2]/'runtime.json').exists()
        after = confinement_snapshot(); result['confinement_after'] = after
        browser_after = next(entry for entry in inventory() if entry['path'] == str(SYSTEM_CHROME))
        result['selected_system_browser_after'] = browser_after
        result['system_browser_and_confinement_unchanged'] = before == after and browser_after == selected
        if not result['system_browser_and_confinement_unchanged']:
            result['system_state_gate'] = 'OPEN: observed system state changed'
    except Exception as error:
        result['probe_error'] = error_message(error)
    finally:
        if before is not None and 'confinement_after' not in result:
            try:
                result['confinement_after'] = confinement_snapshot()
                result['system_browser_and_confinement_unchanged'] = before == result['confinement_after'] and digest(SYSTEM_CHROME) == result.get('selected_system_browser_sha256')
            except Exception as error:
                result['post_audit_error'] = error_message(error)
        (fixture / 'system-browser-vm-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            statuses = {'PASS', 'OPEN', 'BLOCKED', 'NOT_RUN'}
            lane_status = {name: result[name].get('status', 'OPEN') for name in ('UI_lane', 'source_lane')}
            for name, value in lane_status.items():
                if value not in statuses:
                    lane_status[name] = 'OPEN'
            with Path(summary).open('a', encoding='utf-8') as stream:
                stream.write('\nExisting system browser probe (transport result only)\n\n' +
                    '- UI backend: ' + ('exact frozen package' if executable else 'source Python') + '\n' +
                    '- Own XDG/Xvfb UI lane: ' + lane_status['UI_lane'] + '\n' +
                    '- Independent synthetic source lane: ' + lane_status['source_lane'] + '\n' +
                    '- Actual desktop acceptance: not claimed\n' +
                    '- Whole Linux acceptance: not claimed\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--expected-source')
    parser.add_argument('--expected-executable-sha256')
    arguments = parser.parse_args()
    try:
        run(arguments.executable, arguments.expected_source, arguments.expected_executable_sha256)
    except Exception as error:
        print(json.dumps({'scope': 'system browser VM preflight', 'status': 'REFUSED', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
