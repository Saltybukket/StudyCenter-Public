"""Public-owned Windows shortcuts and cooperative restart for one data instance."""
import base64
import os
from pathlib import Path
import subprocess
import sys


def _windows():
    if sys.platform != 'win32':
        raise ValueError('Diese Desktop-Funktion ist nur unter Windows verfügbar.')


def _startup(paths):
    return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup' / ('StudyCenter-Public-' + paths.instance_id + '.lnk')


def autostart_enabled(paths):
    return sys.platform == 'win32' and _startup(paths).is_file()


def _shortcut(path, paths, background=False):
    _windows()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(os.environ.get('STUDYCENTER_PUBLIC_INSTALL_ROOT', ''))
    starter = root / 'start.cmd'
    args = ['--data-dir', str(paths.data)] + (['--background'] if background else [])
    if root.is_absolute() and starter.is_file():
        target = str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe')
        arguments = '/d /c call ' + subprocess.list2cmdline([str(starter), *args])
        working = str(root)
    else:
        if getattr(sys, 'frozen', False):
            target = sys.executable
            arguments = subprocess.list2cmdline(args)
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            if not pythonw.is_file():
                raise ValueError('pythonw.exe fehlt; manuellen Start verwenden.')
            target = str(pythonw)
            arguments = subprocess.list2cmdline(['-m', 'studycenter_public', *args])
        working = str(paths.data)
    def quote(value): return "'" + str(value).replace("'", "''") + "'"
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({quote(path)});"
              f"$s.TargetPath={quote(target)};$s.Arguments={quote(arguments)};"
              f"$s.WorkingDirectory={quote(working)};$s.Save()")
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                   check=True, timeout=15, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def set_autostart(enabled, paths):
    _windows()
    path = _startup(paths)
    if enabled: _shortcut(path, paths, background=True)
    else: path.unlink(missing_ok=True)


def install_desktop(paths):
    _windows()
    import winreg
    programs = _startup(paths).parent.parent
    name = 'StudyCenter-Public-' + paths.instance_id + '.lnk'
    menu = programs / name
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
        desktop = Path(os.path.expandvars(winreg.QueryValueEx(key, 'Desktop')[0]))
    _shortcut(menu, paths)
    _shortcut(desktop / name, paths)
    return str(menu)


def launch_restart(data_dir):
    import json
    from urllib.parse import urlsplit
    port = 8767
    try:
        endpoint = json.loads((Path(data_dir) / 'runtime.json').read_text(encoding='utf-8'))['url']
        parsed = urlsplit(endpoint)
        if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port:
            port = parsed.port
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if getattr(sys, 'frozen', False):
        command = [sys.executable]
    elif sys.platform == 'win32':
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        if not pythonw.is_file(): raise OSError('pythonw.exe fehlt; StudyCenter bleibt geoeffnet.')
        command = [str(pythonw), '-m', 'studycenter_public']
    else:
        command = [sys.executable, '-m', 'studycenter_public']
    arguments = [*command, '--data-dir', str(data_dir), '--port', str(port)]
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if sys.platform == 'win32':
        arguments += ['--after-process', str(os.getpid())]
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return subprocess.Popen(arguments, **options).pid
    if sys.platform != 'linux' or not hasattr(os, 'pidfd_open'):
        raise ValueError('Sicherer Neustart ist auf diesem System nicht verfuegbar.')
    try:
        pidfd = os.pidfd_open(os.getpid(), 0)
    except OSError as error:
        raise OSError('Sicherer Neustart braucht pidfd-Unterstuetzung; StudyCenter bleibt geoeffnet.') from error
    try:
        arguments += ['--after-pidfd', str(pidfd)]
        options.update(pass_fds=(pidfd,), start_new_session=True)
        return subprocess.Popen(arguments, **options).pid
    finally:
        os.close(pidfd)
