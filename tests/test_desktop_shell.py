import json
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from studycenter_public.desktop import shell


def desktop(tmp_path):
    runtime = SimpleNamespace(url='http://127.0.0.1:43123', paths=SimpleNamespace(data=tmp_path), stop=lambda: None)
    app = SimpleNamespace(state=SimpleNamespace(csrf='synthetic-token'))
    return shell.Desktop(runtime, app)


def test_close_hides_dispatches_cleanup_and_restores_owned_bounds(tmp_path):
    view = desktop(tmp_path)
    finished = threading.Event()
    calls = []
    class Window:
        width, height, x, y = 1150, 810, 30, 40
        def hide(self): calls.append('hide')
        def evaluate_js(self, script):
            calls.append(script)
            finished.set()
    view.window = Window()
    view.visible = True
    assert view.closing() is False
    assert finished.wait(3)
    # Wait for the atomic save after JS has returned.
    with view.hide_gate:
        pass
    assert calls == ['hide', "window.dispatchEvent(new Event('studycenter-hide'))"]
    assert view.visible is False
    assert json.loads(view.bounds_path.read_text(encoding='utf-8')) == {'width': 1150, 'height': 810, 'x': 30, 'y': 40}
    assert view.bounds()['width'] == 1150
    view.bounds_path.write_text('{"width": false, "height": 999999, "x": 7, "other": 1}')
    assert view.bounds() == {'x': 7}
    view.stopping = True
    assert view.closing() is True


def test_navigation_stays_on_actual_public_origin(tmp_path, monkeypatch):
    view = desktop(tmp_path)
    opened = []
    monkeypatch.setattr(shell, 'open_system_browser', opened.append)
    def navigate(url):
        args = SimpleNamespace(Uri=url, Cancel=False)
        view.navigation(None, args)
        return args.Cancel
    assert not navigate('http://127.0.0.1:43123/settings')
    assert navigate('http://127.0.0.1:43124/overview')
    assert navigate('https://example.test/path')
    assert navigate('file:///sensitive')
    assert opened == ['http://127.0.0.1:43124/overview', 'https://example.test/path']


def test_tray_commands_use_current_instance_and_csrf(tmp_path, monkeypatch):
    view = desktop(tmp_path)
    calls, done = [], threading.Event()
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *args): done.set()
        def post(self, url, headers):
            calls.append((url, headers))
            return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(shell.httpx, 'Client', Client)
    view.command('/api/sync')
    assert done.wait(3)
    with view.command_gate:
        pass
    assert calls == [('http://127.0.0.1:43123/api/sync',
                      {'Origin': view.runtime.url, 'X-CSRF-Token': 'synthetic-token'})]


def test_show_navigates_selected_path_and_quit_is_idempotent(tmp_path):
    view = desktop(tmp_path)
    calls = []
    destroyed = threading.Event()
    def destroy():
        calls.append('destroy')
        destroyed.set()
    view.window = SimpleNamespace(show=lambda: calls.append('show'), restore=lambda: calls.append('restore'),
        load_url=lambda value: calls.append(value), destroy=destroy)
    view.tray = SimpleNamespace(stop=lambda: calls.append('tray stop'))
    view.runtime.stop = lambda: calls.append('runtime stop')
    view.show('/overview#voice')
    assert calls[:2] == ['show', 'restore']
    parsed = urlsplit(calls[2])
    assert (parsed.netloc, parsed.path, parsed.fragment) == ('127.0.0.1:43123', '/overview', 'voice')
    assert parsed.query.startswith('sc_window=')
    view.quit()
    view.quit()
    assert destroyed.wait(3)
    assert calls[3:].count('runtime stop') == calls[3:].count('destroy') == calls[3:].count('tray stop') == 1


def test_shell_wires_tray_actions_and_guard_before_window_runs(tmp_path, monkeypatch):
    import sys
    from studycenter_public.platform import notifications
    monkeypatch.setattr(notifications, 'desktop_notify', notifications.desktop_notify)
    view = desktop(tmp_path)
    stopped = threading.Event()
    view.runtime.stop = stopped.set
    view.runtime.thread = SimpleNamespace(join=lambda: stopped.wait(3), is_alive=lambda: not stopped.is_set())
    monkeypatch.setattr(shell.ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda value: None)), raising=False)
    class Event:
        def __init__(self): self.handlers = []
        def __iadd__(self, handler): self.handlers.append(handler); return self
    class Item:
        def __init__(self, text, action, **kwargs): self.text, self.action = text, action
    class Tray:
        def __init__(self, name, image, title, menu): self.menu = menu
        def run(self): pass
        def stop(self): pass
        def notify(self, *args): pass
    fake_pystray = SimpleNamespace(Icon=Tray, Menu=lambda *items: items, MenuItem=Item)
    monkeypatch.setitem(sys.modules, 'pystray', fake_pystray)
    navigation = Event()
    events = SimpleNamespace(closing=Event(), before_show=Event(), shown=Event(), loaded=Event())
    paths = []
    destroys, first_destroy = [], threading.Event()
    def destroy():
        destroys.append(True)
        if len(destroys) == 1:
            first_destroy.set()
            raise RuntimeError('Synthetic native window has not started')
    window = SimpleNamespace(events=events, native=SimpleNamespace(webview=SimpleNamespace(NavigationStarting=navigation)),
        show=lambda: None, restore=lambda: None, load_url=paths.append, destroy=destroy)
    fake_webview = SimpleNamespace(settings={}, create_window=lambda *args, **kwargs: window)
    completed = []
    def start(**kwargs):
        events.before_show.handlers[0]()
        assert navigation.handlers == [view.navigation]
        assert fake_webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] is False
        assert [item.text for item in view.tray.menu] == ['Dashboard', 'Luna', 'TU-Sync jetzt',
            'Einstellungen', 'Status', 'Neustart', 'Vollständig beenden']
        commands = []
        monkeypatch.setattr(view, 'command', commands.append)
        for item in view.tray.menu[:-1]: item.action(None, None)
        assert commands == ['/api/sync', '/settings/restart']
        assert [urlsplit(path).path for path in paths] == ['/overview', '/overview', '/settings', '/settings']
        view.tray.menu[-1].action(None, None)
        assert first_destroy.wait(3)
        events.shown.handlers[0]()
        assert len(destroys) == 2
        completed.append(True)
    fake_webview.start = start
    monkeypatch.setitem(sys.modules, 'webview', fake_webview)
    view.run()
    assert stopped.is_set() and completed == [True]


@pytest.mark.parametrize('creation_fails', [False, True])
def test_quit_during_window_creation_cannot_start_orphaned_webview(tmp_path, monkeypatch, creation_fails):
    import sys
    from studycenter_public.platform import notifications
    monkeypatch.setattr(notifications, 'desktop_notify', notifications.desktop_notify)
    view = desktop(tmp_path)
    stopped, started = threading.Event(), []
    view.runtime.stop = stopped.set
    view.runtime.thread = SimpleNamespace(join=lambda: stopped.wait(3), is_alive=lambda: not stopped.is_set())
    monkeypatch.setattr(shell.ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda value: None)), raising=False)
    class Tray:
        def __init__(self, *args, **kwargs): pass
        def run(self): pass
        def stop(self): pass
        def notify(self, *args): pass
    monkeypatch.setitem(sys.modules, 'pystray', SimpleNamespace(Icon=Tray, Menu=lambda *args: args,
        MenuItem=lambda *args, **kwargs: None))
    def create_window(*args, **kwargs):
        view.quit()  # Backend quit arrives while the window object is being constructed.
        if creation_fails:
            raise RuntimeError('Synthetic window creation failure after quit')
        return SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'webview', SimpleNamespace(settings={}, create_window=create_window,
        start=lambda **kwargs: started.append(True)))
    monkeypatch.setattr(shell, 'open_system_browser', lambda url: started.append(url))
    view.run()
    view.show('/settings')
    view.command('/api/sync')
    assert stopped.is_set() and started == [] and view.window is None
