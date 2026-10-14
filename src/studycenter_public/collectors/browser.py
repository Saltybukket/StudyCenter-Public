"""Own installation profile and explicit visible institutional login."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..platform.paths import restrict_permissions
from .web import LoginRequired, UnsafeURL, mutating_url, resolve_public, validate_addresses, safe_url

SERVICE_URLS = {'teachcenter': 'https://tc.tugraz.at/main/my/',
                'tugrazonline': 'https://online.tugraz.at/tug_online/',
                'webmail': 'https://webmail.tugraz.at/imp/dynamic.php?page=mailbox'}
INSTITUTION_HOSTS = {'tc.tugraz.at', 'online.tugraz.at', 'webmail.tugraz.at', 'sso.tugraz.at', 'idp.tugraz.at', 'auth.tugraz.at', 'www.tugraz.at'}


class BrowserUnavailable(RuntimeError):
    pass


def session_signal(kind, raw, url):
    soup = BeautifulSoup(raw, 'html.parser')
    parts = urlsplit(url)
    expected = urlsplit(SERVICE_URLS.get(kind, SERVICE_URLS['teachcenter'])).hostname
    if parts.hostname != expected or soup.select_one('input[type=password],form#login') or any(word in parts.path.lower() for word in ('/login', '/auth/', '/sso/', '/my.policy')):
        return 'login_required'
    if kind == 'teachcenter':
        user = soup.select_one('.usermenu [data-userid], .usermenu .userbutton, a[href*="logout.php"], [data-region="user-menu"]')
        content = soup.select_one('#region-main, [data-region="course-content"], .course-content, [data-region="myoverview"], [data-region="coursesync"]')
        return 'authenticated' if user and content else 'unknown'
    if kind == 'tugrazonline':
        return 'authenticated' if soup.select_one('#id-ca-sidenav-logout,.profile-username') else 'unknown'
    return 'authenticated' if 'DimpCore' in str(raw) else 'unknown'


class BrowserManager:
    def __init__(self, paths):
        self.paths = paths
        self.profile = Path(paths.profiles) / 'tu-browser'
        self.context = None
        self.playwright = None
        self.visible = False
        self.lock = asyncio.Lock()
        self._allowed_hosts = set()

    async def _launch(self, visible):
        await self._close_context()
        from ..platform.components import configure_browser_runtime
        configure_browser_runtime(self.paths)
        self.profile.mkdir(parents=True, exist_ok=True)
        restrict_permissions(self.profile)
        try:
            from playwright.async_api import async_playwright
            mappings = []
            self._allowed_hosts.clear()
            for host in sorted(INSTITUTION_HOSTS):
                try:
                    address = validate_addresses(await asyncio.to_thread(resolve_public, host))
                    mappings.append('MAP ' + host + ' ' + address)
                    self._allowed_hosts.add(host)
                except (OSError, ValueError):
                    continue
            if not self._allowed_hosts:
                raise BrowserUnavailable('Institutionelle Dienste derzeit nicht erreichbar')
            mappings.append('MAP * ~NOTFOUND')
            self.playwright = await async_playwright().start()
            # Default headless Chromium uses Playwright's dedicated Headless Shell.
            # Setting channel='chromium' would select the full browser instead.
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile), headless=not visible, accept_downloads=True,
                args=['--no-proxy-server', '--host-resolver-rules=' + ','.join(mappings)])
            self.visible = visible
            if not visible:
                await self.context.route('**/*', self._readonly_route)
            return self.context
        except Exception as error:
            await self._close_context()
            if isinstance(error, BrowserUnavailable):
                raise
            raise BrowserUnavailable('Eigener Browser nicht verfügbar; Chromium und Chromium Headless Shell für diese Installation installieren. Kein anderer Browser wird verwendet.') from None

    async def _readonly_route(self, route):
        request = route.request
        try:
            parts = urlsplit(safe_url(request.url, preserve_fragment=True))
            if parts.hostname not in self._allowed_hosts or request.method not in ('GET', 'HEAD'):
                raise UnsafeURL('Nicht freigegebene Browseranfrage')
            if request.resource_type in ('media', 'websocket') or mutating_url(request.url):
                raise UnsafeURL('Schreibaktion oder Medien ausgeschlossen')
            # Chromium's resolver rules pin these origins to the prevalidated IPs.
            await route.continue_()
        except ValueError:
            await route.abort()

    async def login(self, url=None, *, explicit=False, service='teachcenter'):
        if not explicit:
            raise ValueError('Sichtbare Anmeldung braucht eine ausdrückliche Nutzeraktion')
        url = safe_url(url or SERVICE_URLS[service], preserve_fragment=True)
        if urlsplit(url).hostname not in INSTITUTION_HOSTS:
            raise UnsafeURL('Anmeldung nur beim ausgewählten institutionellen Dienst')
        async with self.lock:
            context = await self._launch(True)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=35000)
            return {'visible': True, 'service': service, 'message': 'Im eigenen Browser anmelden; danach manuell synchronisieren.'}

    @asynccontextmanager
    async def session(self):
        async with self.lock:
            context = await self._launch(False)
            try:
                yield context
            finally:
                await self._close_context()

    async def _close_context(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = None
            self.visible = False
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

    async def close(self):
        async with self.lock:
            await self._close_context()
