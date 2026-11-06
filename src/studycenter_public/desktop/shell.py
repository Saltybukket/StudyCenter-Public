"""Windows WebView2 and tray for this Public instance only."""
import ctypes
import json
import logging
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ..platform.paths import atomic_json, owned_path
from .browser import open_system_browser


class Desktop:
    def __init__(self, runtime, app):
        self.runtime, self.app = runtime, app
        self.window = self.tray = None
        self.stopping = False
        self.visible = False
        self.path = '/overview'
        self.retries = 0
        self.hide_gate = threading.Lock()
        self.quit_gate = threading.Lock()
        self.command_gate = threading.Lock()
        self.bounds_path = owned_path(runtime.paths.data / 'desktop-window.json', runtime.paths.data)

    def url(self, path):
        before, marker, fragment = path.partition('#')
        separator = '&' if '?' in before else '?'
        return self.runtime.url + before + separator + 'sc_window=' + uuid4().hex + (marker + fragment if marker else '')

    def show(self, path='/overview'):
        with self.quit_gate:
            if self.stopping:
                return
            self.path, self.visible, self.retries = path, True, 0
            window = self.window
        if window:
            try:
                window.show()
                window.restore()
                window.load_url(self.url(path))
                return
            except Exception:
                logging.getLogger(__name__).exception('App-Fenster konnte nicht geöffnet werden.')
        if not self.stopping:
            open_system_browser(self.runtime.url + path)

    def command(self, path):
        with self.quit_gate:
            if self.stopping or not self.command_gate.acquire(blocking=False):
                return
        def run():
            try:
                if self.stopping:
                    return
                with httpx.Client(timeout=10, trust_env=False) as client:
                    response = client.post(self.runtime.url + path,
                        headers={'Origin': self.runtime.url, 'X-CSRF-Token': self.app.state.csrf})
                    response.raise_for_status()
            except Exception as error:
                if self.tray and not self.stopping:
                    try:
                        detail = response.json().get('detail') if 'response' in locals() else None
                    except (ValueError, AttributeError):
                        detail = None
                    self.tray.notify(str(detail or error)[:200], 'StudyCenter Public')
            finally:
                self.command_gate.release()
        threading.Thread(target=run, daemon=True).start()

    def quit(self, icon=None, item=None):
        with self.quit_gate:
            if self.stopping:
                return
            self.stopping = True
        self.runtime.stop()
        if self.window:
            # pywebview can wait for native initialization; never block the
            # server event loop that must finish shutdown while it waits.
            threading.Thread(target=self.destroy_window, args=(self.window,), daemon=True).start()
        if self.tray:
            self.tray.stop()

    def destroy_window(self, window):
        try: window.destroy()
        except Exception: logging.getLogger(__name__).exception('Fenster konnte nicht geschlossen werden.')

    def closing(self):
        if self.stopping:
            return True
        bounds = {key: getattr(self.window, key) for key in ('width', 'height', 'x', 'y')}
        self.visible = False
        self.window.hide()
        # FormClosing is on the UI thread; JS evaluation must happen afterwards.
        def cleanup():
            try:
                self.window.evaluate_js("window.dispatchEvent(new Event('studycenter-hide'))")
            except Exception:
                logging.getLogger(__name__).exception('Fensterfreigabe fehlgeschlagen.')
            try:
                atomic_json(self.bounds_path, bounds)
            except (OSError, ValueError):
                logging.getLogger(__name__).exception('Fenstergröße konnte nicht gespeichert werden.')
            finally:
                self.hide_gate.release()
        if self.hide_gate.acquire(blocking=False):
            threading.Thread(target=cleanup, daemon=True).start()
        return False

    def bounds(self):
        try:
            value = json.loads(self.bounds_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('invalid bounds')
            return {k: v for k, v in value.items() if k in ('width', 'height', 'x', 'y')
                    and type(v) is int and (k not in ('width', 'height') or 320 <= v <= 16384)}
        except (OSError, ValueError):
            return {}

    def navigation(self, sender, args):
        address = urlsplit(str(args.Uri))
        origin = urlsplit(self.runtime.url)
        if address.scheme == origin.scheme and address.netloc == origin.netloc:
            return
        args.Cancel = True
        if address.scheme in ('http', 'https'):
            open_system_browser(str(args.Uri))

    def loaded(self):
        try:
            health = self.window.evaluate_js("({ready:!!document.querySelector('main h1')})")
            if isinstance(health, str):
                health = json.loads(health)
            if isinstance(health, dict) and health.get('ready'):
                self.load_ready.set()
            elif self.visible and self.retries < 1:
                self.retries += 1
                def retry():
                    if not self.stopping and self.visible:
                        try: self.window.load_url(self.url(self.path))
                        except Exception: logging.getLogger(__name__).exception('Erneutes Laden fehlgeschlagen.')
                timer = threading.Timer(1, retry)
                timer.daemon = True
                timer.start()
        except Exception:
            logging.getLogger(__name__).exception('Fensterladeprüfung fehlgeschlagen.')

    def run(self, start_hidden=False):
        import pystray
        from PIL import Image, ImageDraw
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('StudyCenter.Public')
        icon_image = Image.new('RGBA', (64, 64), '#16655b')
        drawing = ImageDraw.Draw(icon_image)
        drawing.rounded_rectangle((12, 10, 52, 54), radius=4, fill='#fafbfc')
        for y in (23, 32, 41): drawing.line((20, y, 44, y), fill='#16655b', width=3)
        self.tray = pystray.Icon('StudyCenter.Public', icon_image, 'StudyCenter Public', menu=pystray.Menu(
            pystray.MenuItem('Dashboard', lambda icon, item: self.show(), default=True),
            pystray.MenuItem('Luna', lambda icon, item: self.show('/overview#voice')),
            pystray.MenuItem('TU-Sync jetzt', lambda icon, item: self.command('/api/sync')),
            pystray.MenuItem('Einstellungen', lambda icon, item: self.show('/settings')),
            pystray.MenuItem('Status', lambda icon, item: self.show('/settings#background')),
            pystray.MenuItem('Neustart', lambda icon, item: self.command('/settings/restart')),
            pystray.MenuItem('Vollständig beenden', self.quit)))
        from ..platform import notifications
        notifications.desktop_notify = lambda item, endpoint: self.tray.notify(str(item.get('title', 'Erinnerung')), 'StudyCenter Public')
        self.app.state.stop_callback = self.quit
        threading.Thread(target=self.tray.run, daemon=True).start()
        def watch_server():
            self.runtime.thread.join()
            self.quit()
        threading.Thread(target=watch_server, daemon=True).start()
        try:
            import webview
            # Route popup requests through NavigationStarting too; only HTTP(S)
            # destinations are handed to the external browser by our guard.
            webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
            self.visible = not start_hidden
            self.load_ready = threading.Event()
            window = webview.create_window('StudyCenter Public', self.url('/overview'),
                min_size=(760, 550), hidden=start_hidden, **({'width': 1220, 'height': 840} | self.bounds()))
            with self.quit_gate:
                if self.stopping:
                    return
                self.window = window
            self.window.events.closing += self.closing
            def guard():
                self.window.native.webview.NavigationStarting += self.navigation
            self.window.events.before_show += guard
            def shown():
                if self.stopping:
                    self.destroy_window(window)
            self.window.events.shown += shown
            self.window.events.loaded += self.loaded
            def watchdog():
                if not self.load_ready.wait(45) and not self.stopping:
                    self.tray.notify('Das App-Fenster ist nicht bereit. Der Systembrowser kann verwendet werden.', 'StudyCenter Public')
                    if self.visible:
                        open_system_browser(self.runtime.url + self.path)
            threading.Thread(target=watchdog, daemon=True).start()
            webview.start(gui='edgechromium', private_mode=True)
        except Exception:
            logging.getLogger(__name__).exception('WebView2 nicht verfügbar.')
            self.window = None
            if not self.stopping:
                if not start_hidden: open_system_browser(self.runtime.url)
                self.tray.notify('WebView2 nicht verfügbar. Der Systembrowser kann verwendet werden.', 'StudyCenter Public')
        finally:
            if hasattr(self, 'load_ready'):
                self.load_ready.set()
            if self.runtime.thread.is_alive():
                self.runtime.thread.join()
            self.quit()


def run_shell(runtime, app, start_hidden=False):
    Desktop(runtime, app).run(start_hidden)
