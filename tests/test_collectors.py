import httpx
import pytest

from studycenter_public.collectors.web import PublicCollector, CrawlPolicy, UnsafeURL, collect
from studycenter_public.collectors.browser import session_signal
from studycenter_public.collectors.tu import personal_course_links
from studycenter_public.collectors.mail import IMAPCollector, WebmailCollector, MailError


@pytest.mark.asyncio
async def test_public_crawl_pins_dns_retains_pages_and_excludes_media():
    calls = []
    async def serve(request):
        calls.append(str(request.url))
        assert request.headers['host'] == 'example.org'
        assert request.url.host == '93.184.216.34'
        if request.url.path == '/course/':
            return httpx.Response(200, headers={'content-type':'text/html'}, content=b'<main>Welcome<a href="page">Read</a><a href="movie.mp4">Movie</a><a href="/logout">Logout</a></main>')
        return httpx.Response(200, headers={'content-type':'text/plain'}, content=b'Complete course page')
    collector = PublicCollector(client=httpx.AsyncClient(transport=httpx.MockTransport(serve)), resolver=lambda host: ['93.184.216.34'])
    result = await collect('https://example.org/course/', collector, policy=CrawlPolicy(max_pages=3))
    assert result.complete
    assert [document.raw for document in result.documents] == [b'<main>Welcome<a href="page">Read</a><a href="movie.mp4">Movie</a><a href="/logout">Logout</a></main>', b'Complete course page']
    assert len(calls) == 2
    await collector.close()


def test_institutional_discovery_retains_course_identity_without_cookie_claim():
    html = '<div class="usermenu"><div data-userid="7"></div></div><main id="region-main"><div data-region="coursesync"><a href="/main/course/view.php?id=9"><span class="card-title">Synthetic mathematics</span></a></div></main>'
    assert session_signal('teachcenter', html, 'https://tc.tugraz.at/main/my/') == 'authenticated'
    assert session_signal('teachcenter', '<main id="region-main">Guest</main>', 'https://tc.tugraz.at/main/my/') == 'unknown'
    assert personal_course_links(html, 'https://tc.tugraz.at/main/my/', 'teachcenter') == [{'external_id':'9', 'title':'Synthetic mathematics', 'url':'https://tc.tugraz.at/main/course/view.php?id=9', 'kind':'teachcenter'}]
    policy = CrawlPolicy(kind='tugrazonline')
    assert not policy.allows('https://online.tugraz.at/tug_online/#/student/courses/12', 'https://online.tugraz.at/tug_online/#/student/courses/9')


def test_imap_peek_never_changes_mailbox_flags():
    import json
    raw = b'Subject: Synthetic course\r\nFrom: sender@example.org\r\nDate: Tue, 13 Oct 2026 08:00:00 +0000\r\nMessage-ID: <synthetic@example.org>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nRetained course message.'
    calls = []
    class Secrets:
        def get(self, name):
            return json.dumps({'host':'mail.tugraz.at', 'username':'synthetic', 'password':'synthetic'})
    class Client:
        def login(self, user, password):
            calls.append('LOGIN')
        def select(self, folder, readonly=False):
            assert readonly
            calls.append('EXAMINE')
            return 'OK', []
        def response(self, name):
            return 'OK', [b'123']
        def uid(self, command, *args):
            calls.append(command + ' ' + str(args))
            if command == 'SEARCH':
                return 'OK', [b'5']
            return 'OK', [(f'5 RFC822.SIZE {len(raw)}'.encode(), raw)]
        def logout(self):
            calls.append('LOGOUT')
    result, cursor = IMAPCollector(Secrets(), client_factory=Client).collect({'keywords':['Synthetic course'], 'since':'2020-01-01'})
    assert result.complete
    assert result.documents[0].raw == raw
    assert cursor == {'uidvalidity':'123', 'last_uid':5}
    assert any('BODY.PEEK[]' in call for call in calls)
    assert all('STORE' not in call and 'CLOSE' not in call for call in calls)


@pytest.mark.asyncio
async def test_webmail_raw_read_checks_flags_without_preview(monkeypatch):
    import json
    import studycenter_public.collectors.mail as module
    monkeypatch.setattr(module, 'resolve_public', lambda host:['93.184.216.34'])
    raw = b'Subject: Synthetic course\r\nFrom: sender@example.org\r\nDate: Tue, 13 Oct 2026 08:00:00 +0000\r\nMessage-ID: <synthetic@example.org>\r\n\r\nRetained message.'
    calls = []
    async def serve(request):
        calls.append(request)
        if request.url.path == '/imp/dynamic.php':
            configs = b'<script>HordeCore.conf = {"TOKEN":"synthetic-token","URI_AJAX":"/services/ajax.php/imp/"};DimpCore.conf = {"qsearchid":"synthetic-search"};</script>'
            return httpx.Response(200, content=configs)
        if request.url.path.endswith('viewPort'):
            body = (await request.aread()).decode()
            assert 'msgload' not in body and 'sortby' not in body and 'applyfilter' not in body
            return httpx.Response(200, json={'tasks':{'imp:viewport':{'totalrows':1,'data':{'4':{'flag':[]}}}}})
        assert 'actionID=view_source' in str(request.url)
        return httpx.Response(200, headers={'content-type':'message/rfc822'}, content=raw)
    class Context:
        async def cookies(self, url):
            return []
    collector = WebmailCollector(Context())
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    result, cursor = await collector.collect({'keywords':['Synthetic course'], 'since':'2020-01-01'})
    assert result.complete
    assert result.documents[0].raw == raw
    assert result.documents[0].url.startswith('urn:mail:')
    assert len([request for request in calls if request.url.path.endswith('viewPort')]) == 2
    await collector.close()


@pytest.mark.asyncio
async def test_dns_private_address_and_out_of_scope_redirect_are_rejected():
    private = PublicCollector(resolver=lambda host: ['127.0.0.1'])
    with pytest.raises(UnsafeURL):
        await private.fetch('https://example.org/course/', 'https://example.org/course/', CrawlPolicy())
    await private.close()
    async def redirect(request):
        return httpx.Response(302, headers={'location':'https://elsewhere.org/secrets'})
    collector = PublicCollector(client=httpx.AsyncClient(transport=httpx.MockTransport(redirect)), resolver=lambda host: ['93.184.216.34'])
    with pytest.raises(UnsafeURL):
        await collector.fetch('https://example.org/course/', 'https://example.org/course/', CrawlPolicy())
    await collector.close()


@pytest.mark.asyncio
async def test_cancel_retains_pending_links_and_encoded_mutations_are_excluded():
    import asyncio
    cancellation = asyncio.Event()
    async def serve(request):
        return httpx.Response(200, headers={'content-type':'text/html'}, content=b'<main>Read<a href="next">Next</a><a href="%65nrol.php">Enrol</a></main>')
    async def progress(state):
        cancellation.set()
    collector = PublicCollector(client=httpx.AsyncClient(transport=httpx.MockTransport(serve)), resolver=lambda host:['93.184.216.34'])
    result = await collect('https://example.org/course/', collector, cancel=cancellation, progress=progress)
    assert result.cancelled
    assert not result.complete
    assert result.pending == [('https://example.org/course/next', 1)]
    assert 'https://example.org/course/%65nrol.php' in result.excluded
    await collector.close()


@pytest.mark.parametrize('visible', [False, True])
def test_own_browser_always_requires_chromium_sandbox(monkeypatch, tmp_path, visible):
    from types import SimpleNamespace
    import playwright.async_api
    import studycenter_public.collectors.browser as browser
    import studycenter_public.platform.components as components

    data = tmp_path / 'data'
    profiles = data / 'profiles'
    profiles.mkdir(parents=True)
    options = []

    class Context:
        async def route(self, *args):
            pass
        async def close(self):
            pass

    class Chromium:
        async def launch_persistent_context(self, profile, **kwargs):
            options.append(kwargs)
            assert profile == str(profiles / 'tu-browser')
            return Context()

    class Playwright:
        chromium = Chromium()
        async def stop(self):
            pass

    class Starter:
        async def start(self):
            return Playwright()

    async def immediate_thread(function, *args):
        return function(*args)

    def complete_immediately(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
        raise AssertionError('Options seam unexpectedly attempted background I/O')

    monkeypatch.setattr(playwright.async_api, 'async_playwright', Starter)
    monkeypatch.setattr(components, 'configure_browser_runtime', lambda paths: None)
    monkeypatch.setattr(browser, 'restrict_permissions', lambda path: None)
    monkeypatch.setattr(browser, 'resolve_public', lambda host: ['93.184.216.34'])
    monkeypatch.setattr(browser.asyncio, 'to_thread', immediate_thread)
    manager = browser.BrowserManager(SimpleNamespace(data=data, profiles=profiles))
    complete_immediately(manager._launch(visible))
    assert len(options) == 1
    assert options[0].get('chromium_sandbox') is True
    assert options[0]['headless'] is (not visible)
    assert 'channel' not in options[0]
    assert not any('no-sandbox' in argument or 'disable-setuid-sandbox' in argument
                   for argument in options[0]['args'])
    complete_immediately(manager.close())
