"""Exercise cancellation of the *exact* Windows installer on a disposable runner.

No installer or app is executed until the hosted-runner and registry guards pass.
The second probe induces a genuine sharing failure at a late file and clicks the
installer's error-dialog Cancel/Abort. It is deliberately reported separately
from an ordinary click on the wizard Cancel button.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from uuid import uuid4

import smoke_slim

APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}_is1'


def require_runner():
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Real installer probes require an existing disposable GitHub-hosted Windows runner')
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, 0, winreg.KEY_READ | view):
                raise RuntimeError('An application with this installation identity already exists; refusing to run')
        except FileNotFoundError:
            pass
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    if not temp.is_dir():
        raise RuntimeError('Runner temporary directory unavailable')
    return temp


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def program_snapshot(program):
    result = {}
    for directory, subdirs, files in os.walk(program, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in list(subdirs):
            path = parent/name
            if path.is_symlink() or path.is_junction():
                raise RuntimeError('Unexpected reparse point in synthetic fixture')
            if parent == program and name == '.local':
                subdirs.remove(name)
        for name in files:
            path = parent/name
            if path.is_symlink() or path.is_junction():
                raise RuntimeError('Unexpected reparse point in synthetic fixture')
            result[path.relative_to(program).as_posix()] = digest_file(path)
    for name in ('installation.json', 'python-path.txt'):
        result['.local/'+name] = digest_file(program/'.local'/name)
    return result


def data_snapshot(data):
    result = {}
    for path in data.rglob('*'):
        rel = path.relative_to(data)
        if 'backups' in rel.parts:
            continue  # The installer intentionally creates verified pre-update backups.
        if path.is_symlink() or path.is_junction():
            raise RuntimeError('Unexpected reparse point in synthetic data')
        if path.is_file():
            result[rel.as_posix()] = digest_file(path)
    return result


def verify_synthetic_progress(data):
    connection = sqlite3.connect(data/'core/campusboard.sqlite3')
    try:
        row = connection.execute('SELECT status,pinned,overrides FROM items WHERE identity=?',
                                 ('note:manual:'+'a'*32,)).fetchone()
        if not row or row[:2] != ('done', 1) or json.loads(row[2]).get('description') != 'Synthetic personal correction':
            raise RuntimeError('Synthetic task status, pin or personal edit was lost after restart')
        quest = connection.execute('SELECT state,steps FROM quests WHERE id=?', ('synthetic-quest',)).fetchone()
        if not quest or quest[0] != 'active':
            raise RuntimeError('Synthetic quest was lost after restart')
        steps = json.loads(quest[1])
        if len(steps) != 1 or steps[0].get('done') is not True or steps[0].get('title') != 'Synthetic edited step':
            raise RuntimeError('Synthetic quest progress or manual step edit was lost')
    finally:
        connection.close()
    if json.loads((data/'core/settings.json').read_text(encoding='utf-8')).get('llm_model') != 'synthetic-settings':
        raise RuntimeError('Synthetic local settings changed')
    for name in ('browser-profiles/synthetic-profile/Default/sentinel.txt', 'config/synthetic-credential-sentinel.txt'):
        if (data/name).read_text(encoding='utf-8') != 'synthetic non-secret retained local state':
            raise RuntimeError('Synthetic profile or credential sentinel changed')


def registry_snapshot():
    import winreg
    result = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        i = 0
        while True:
            try:
                name, value, typ = winreg.EnumValue(key, i)
            except OSError:
                break
            result[name] = (value, typ)
            i += 1
    return result


def shortcut_snapshot():
    startup = Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/StudyCenter-Public.lnk'
    group = Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/StudyCenter-Public'
    shell = ctypes.windll.shell32
    buf = ctypes.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, 0x10, None, 0, buf) != 0:
        raise RuntimeError('Unable to locate synthetic runner desktop')
    desktop = Path(buf.value)/'StudyCenter-Public.lnk'
    paths = (startup, desktop, group/'StudyCenter-Public.lnk', group/'Uninstall StudyCenter-Public.lnk')
    return {str(p): digest_file(p) if p.exists() else None for p in paths}


class Win32Wizard:
    """Only message windows belonging to the exact owned setup process tree."""
    def __init__(self, process):
        self.process = process
        self.pids = {process.pid}
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user.GetWindowTextLengthW.restype = ctypes.c_int
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.PostMessageW.restype = wintypes.BOOL
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.user.EnumChildWindows.argtypes = [wintypes.HWND, self.callback_type, wintypes.LPARAM]
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        root_handle = self.kernel.OpenProcess(0x100000, False, process.pid)
        if not root_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles = {process.pid: root_handle}
        class ProcessEntry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
        self.entry = ProcessEntry
        self.kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        self.kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]

    def refresh_pids(self):
        handle = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        rows = []
        try:
            entry = self.entry()
            entry.dwSize = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(handle, ctypes.byref(entry))
            while more:
                rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
                more = self.kernel.Process32NextW(handle, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(handle)
        changed = True
        while changed:
            before = len(self.pids)
            for pid, parent in rows:
                if parent in self.pids and pid not in self.pids and self.kernel.WaitForSingleObject(self.handles[parent], 0) == 0x102:
                    handle = self.kernel.OpenProcess(0x100000, False, pid)
                    if handle:
                        self.handles[pid] = handle
                        self.pids.add(pid)
            changed = before != len(self.pids)

    def close(self):
        for handle in self.handles.values():
            self.kernel.CloseHandle(handle)
        self.handles.clear()

    def text(self, hwnd):
        n = self.user.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n+1)
        self.user.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    def klass(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, buf, len(buf))
        return buf.value

    def windows(self):
        self.refresh_pids()
        found = []
        @self.callback_type
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if (pid.value in self.pids and self.kernel.WaitForSingleObject(self.handles[pid.value], 0) == 0x102
                    and self.user.IsWindowVisible(hwnd)):
                found.append(hwnd)
            return True
        self.user.EnumWindows(visit, 0)
        return found

    def children(self, hwnd):
        found = []
        @self.callback_type
        def visit(child, _):
            if self.user.IsWindowVisible(child):
                found.append((child, self.text(child), self.klass(child), bool(self.user.IsWindowEnabled(child))))
            return True
        self.user.EnumChildWindows(hwnd, visit, 0)
        return found

    @staticmethod
    def normalized(label):
        return label.replace('&', '').replace('...', '').strip().lower()

    def click(self, hwnd):
        if not self.user.IsWindowEnabled(hwnd):
            raise RuntimeError('Refusing to click a disabled wizard control')
        # SendMessage waits through the modal confirmation and deadlocks the
        # driver. Post only to this owned enabled control, then observe results.
        if not self.user.PostMessageW(hwnd, 0x00F5, 0, 0):  # BM_CLICK
            raise ctypes.WinError(ctypes.get_last_error())

    def finish_cancel(self, timeout=40):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return self.process.returncode
            for window in self.windows():
                for control, label, klass, enabled in self.children(window):
                    if enabled and 'button' in klass.lower() and self.normalized(label) in ('yes', 'ja', 'ok'):
                        self.click(control)
            time.sleep(.1)
        raise RuntimeError('Owned installer did not finish after cancellation')

    def cancel_before_prepare(self):
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Installer exited before the pre-install cancellation')
            for window in self.windows():
                for control, label, klass, enabled in self.children(window):
                    if enabled and 'button' in klass.lower() and self.normalized(label) in ('cancel', 'abbrechen'):
                        self.click(control)
                        return self.finish_cancel()
            time.sleep(.1)
        raise RuntimeError('No owned installer wizard Cancel button appeared')

    def drive_until_copy_failure(self, program, baseline, release_lock, *, require_changed):
        deadline = time.monotonic()+240
        disabled_during_prepare = False
        saw_prepare = False
        last_action = 0.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Installer exited before an owned copy-error dialog was observed')
            for window in self.windows():
                children = self.children(window)
                labels = [self.normalized(label) for _, label, _, _ in children]
                combined = ' '.join(labels)
                if 'preparing the public base environment' in combined:
                    saw_prepare = True
                    disabled_during_prepare |= any(self.normalized(label) in ('cancel', 'abbrechen') and not enabled
                                                   for _, label, _, enabled in children)
                has_retry = any(x in ('retry', 'wiederholen') for x in labels)
                if has_retry:
                    changed = sorted(name for name, before in baseline.items()
                                     if not name.startswith(('.local/', 'unins')) and (program/name).is_file()
                                     and digest_file(program/name) != before)
                    if require_changed and not changed:
                        raise RuntimeError('Sharing error appeared before any demonstrably changed old program file')
                    added = sorted(name for name in program_snapshot(program) if name not in baseline
                                   and not name.startswith(('.local/', 'unins')))
                    if not require_changed and (changed or added):
                        raise RuntimeError('Sharing error occurred after program copy began: '+str(changed+added))
                    if digest_file(program/'.local/installation.json') == baseline['.local/installation.json']:
                        raise RuntimeError('Sharing error occurred before new environment preparation was promoted')
                    buttons = [(c, self.normalized(label)) for c, label, klass, enabled in children
                               if enabled and 'button' in klass.lower()]
                    for control, label in buttons:
                        if label in ('cancel', 'abbrechen', 'abort', 'abbrechen der installation'):
                            self.click(control)
                            # Let the genuine error-dialog click land before
                            # releasing the owned lock needed for rollback.
                            release_lock()
                            return self.finish_cancel(), changed, saw_prepare, disabled_during_prepare
                    raise RuntimeError('Sharing-error dialog has no recognizable Cancel/Abort control')
                if time.monotonic()-last_action < .4 or saw_prepare:
                    continue
                buttons = [(c, self.normalized(label)) for c, label, klass, enabled in children
                           if enabled and 'button' in klass.lower()]
                for control, label in buttons:
                    if label.startswith('i accept') or label.startswith('ich akzeptiere'):
                        self.click(control)
                for control, label in buttons:
                    if label in ('next', 'weiter', 'install', 'installieren'):
                        self.click(control)
                        last_action = time.monotonic()
                        break
            time.sleep(.05)
        raise RuntimeError('Timed out before the owned setup copy-error dialog')


def lock_for_reading(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def run(installer, previous_installer, assets, root=None):
    smoke_slim.configure_diagnostics()
    temp = require_runner()
    installer = Path(installer).resolve(strict=True)
    previous_installer = Path(previous_installer).resolve(strict=True)
    assets = Path(assets).resolve(strict=True)
    root = Path(root).resolve() if root is not None else temp/('StudyCenter-cancel-'+uuid4().hex)
    if not root.is_relative_to(temp) or root == temp or root.exists():
        raise RuntimeError('Cancellation fixture must be a new directory inside RUNNER_TEMP')
    root.mkdir()
    result = {'candidate_sha256': digest_file(installer), 'previous_installer_sha256': digest_file(previous_installer),
              'environment': 'disposable GitHub-hosted Windows runner'}
    for phase in ('before_prepare', 'after_prepare_before_copy_error', 'after_file_copy_error'):
        case = root/phase
        case.mkdir()
        program, data = case/'program', case/'data'
        old_env = dict(os.environ, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(previous_installer.parent))
        new_env = dict(os.environ, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(assets))
        for env in (old_env, new_env):
            env.pop('GH_TOKEN', None)
            env.pop('GITHUB_TOKEN', None)
        command = [previous_installer, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                   '/DIR='+str(program), '/DATADIR='+str(data)]
        smoke_slim.run_command(command, old_env)
        assert json.loads((program/'slim-manifest.json').read_text(encoding='utf-8'))['version'] == '0.1.0b5'
        smoke_slim.probe_app(program, data, old_env, {}, create_note=True, expect_manual_note=True)
        smoke_slim.seed_upgrade_evidence(data)
        before_program, before_data = program_snapshot(program), data_snapshot(data)
        before_registry, before_shortcuts = registry_snapshot(), shortcut_snapshot()
        handles = []
        if phase != 'before_prepare':
            # Read-sharing permits preflight and snapshots while denying the
            # installer's replacement of these owned fixture files. For the
            # first-copy case lock all existing bootstrap entries; for the
            # partial-copy case lock only the late start.sh entry.
            names = (('start.cmd', 'start.sh', 'LICENSE', 'README.md', 'setup.py',
                      'slim-manifest.json', 'launch.vbs', 'SYSTEM-RUNTIME-REQUIREMENTS.iss')
                     if phase == 'after_prepare_before_copy_error' else ('start.sh',))
            for name in names:
                if (program/name).is_file():
                    handles.append(lock_for_reading(program/name))
        cmd = [str(installer), '/CURRENTUSER', '/NORESTART', '/DIR='+str(program), '/DATADIR='+str(data),
               '/LOG='+str(case/'candidate-installer.log')]
        process = subprocess.Popen(cmd, env=new_env)
        wizard = Win32Wizard(process)
        def release_lock():
            while handles:
                kernel, handle = handles.pop()
                kernel.CloseHandle(handle)
        try:
            if phase == 'before_prepare':
                code = wizard.cancel_before_prepare()
            else:
                code, changed, saw_prepare, disabled = wizard.drive_until_copy_failure(
                    program, before_program, release_lock, require_changed=phase == 'after_file_copy_error')
                result[phase+'_changed_old_program_members_before_abort'] = changed
                result[phase+'_prepare_caption_observed'] = saw_prepare
                result[phase+'_prepare_cancel_disabled_observed'] = disabled
        finally:
            release_lock()
            wizard.close()
        if code == 0:
            raise RuntimeError('Cancellation/abort was reported as successful installation')
        if program_snapshot(program) != before_program:
            raise RuntimeError('Cancellation did not restore the exact previous synthetic program and pointers')
        if data_snapshot(data) != before_data:
            raise RuntimeError('Cancellation changed synthetic tasks, quests, settings, profiles or credentials')
        if registry_snapshot() != before_registry or shortcut_snapshot() != before_shortcuts:
            raise RuntimeError('Cancellation changed the previous installation registration or shortcuts')
        smoke_slim.run_command(smoke_slim.starter(program, '--self-test', '--no-browser', '--data-dir', str(data)), old_env, timeout=60)
        smoke_slim.probe_app(program, data, old_env, {}, expect_manual_note=True)
        verify_synthetic_progress(data)
        smoke_slim.uninstall(program, old_env, case/'uninstall.log')
        result[phase] = {'exit_code': code, 'previous_program_and_local_data_preserved': True,
                         'previous_registration_shortcuts_and_startup_verified': True}
    (root/'cancel-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--previous-installer', required=True, type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    args = parser.parse_args()
    run(args.installer, args.previous_installer, args.assets)


if __name__ == '__main__':
    main()
