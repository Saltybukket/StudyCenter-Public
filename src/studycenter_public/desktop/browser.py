"""Dispatch the UI to the existing system browser with its native library environment."""
import os
import subprocess
import sys
import webbrowser


def external_browser_environment():
    environment = os.environ.copy()
    if getattr(sys, 'frozen', False) or 'LD_LIBRARY_PATH_ORIG' in environment:
        original = environment.pop('LD_LIBRARY_PATH_ORIG', '')
        if original and getattr(sys, 'frozen', False):
            program = os.path.dirname(os.path.realpath(sys.executable))
            retained = []
            for entry in original.split(os.pathsep):
                if not entry:
                    continue
                try:
                    resolved = os.path.realpath(entry)
                    own = os.path.commonpath([program, resolved]) == program
                except ValueError:
                    own = False
                if not own:
                    retained.append(resolved)
            original = os.pathsep.join(retained)
        if original:
            environment['LD_LIBRARY_PATH'] = original
        else:
            environment.pop('LD_LIBRARY_PATH', None)
    for name in tuple(environment):
        if name.startswith('_PYI_'):
            environment.pop(name)
    return environment


def open_system_browser(url):
    if sys.platform != 'linux':
        return webbrowser.open(url)
    try:
        process = subprocess.Popen(['xdg-open', url], env=external_browser_environment(),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return process.poll() in (None, 0)
