"""Read existing course cards and selected institutional pages only."""
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup

from .browser import session_signal, SERVICE_URLS
from .web import Fetched, LoginRequired, PublicCollector, safe_url, CrawlPolicy


def personal_course_links(raw, page_url, kind):
    soup = BeautifulSoup(raw, 'html.parser')
    result = {}
    if kind == 'teachcenter':
        main = soup.select_one('[data-region="coursesync"], [data-region="myoverview"]')
        if not main:
            return []
        for link in main.select('a[href*="/course/view.php"]'):
            url = safe_url(urljoin(page_url, link['href']))
            external = parse_qs(urlsplit(url).query).get('id', [None])[0]
            if not external or urlsplit(url).netloc != urlsplit(page_url).netloc:
                continue
            heading = BeautifulSoup(str(link.select_one('.card-title,.coursename') or link), 'html.parser')
            for node in heading.select('[data-region="favourite-icon"], .sr-only, .card-subtitle'):
                node.decompose()
            title = heading.get_text(' ', strip=True)
            if title:
                result[external] = {'external_id': external, 'title': title, 'url': url, 'kind': kind}
    else:
        base = soup.find('base', href=True)
        base_url = urljoin(page_url, base['href']) if base else page_url
        for link in soup.select('a[href]'):
            match = re.search(r'/student/courses/(\d+)', link['href'])
            card = link.find_parent('div', class_='list-spacer')
            if not match or not card or not link.get_text(strip=True):
                continue
            text = card.get_text(' ', strip=True)
            course_type = re.search(r'\b(VO|UE|VU|LU)\s*\|', text)
            if not course_type:
                continue
            title = link.get_text(' ', strip=True)
            group = re.search(re.escape(title) + r'\s*-\s*(.+?)\s+(?:VO|UE|VU|LU)\s*\|', text)
            url = urljoin(base_url, link['href'])
            if urlsplit(url).hostname != 'online.tugraz.at':
                continue
            result[match[1]] = {'external_id': match[1], 'title': title, 'url': url,
                'kind': kind, 'course_type': course_type[1], 'group': group[1] if group else '',
                'confirmed': 'Fixplatz zugewiesen' in text,
                'exam_url': next((urljoin(base_url, anchor['href']) for anchor in card.select('a[href]') if anchor.get_text(' ', strip=True) == 'Prüfungstermine'), None)}
    return list(result.values())


class InstitutionalCollector:
    def __init__(self, context, kind):
        self.context, self.kind = context, kind
        self.http = PublicCollector()

    async def fetch(self, url, root, policy):
        if not policy.allows(url, root):
            raise ValueError('Seite außerhalb des ausgewählten Kurses')
        cookies = await self.context.cookies(url)
        # PublicCollector connects to a prevalidated IP and does not follow a
        # redirect outside this source. Only cookies scoped to this URL enter it.
        self.http.client.headers['Cookie'] = '; '.join(cookie['name'] + '=' + cookie['value'] for cookie in cookies)
        if self.kind == 'teachcenter':
            document = await self.http.fetch(url, root, policy)
            if document.mime != 'text/html':
                return document
        page = await self.context.new_page()
        try:
            response = await page.goto(url, wait_until='domcontentloaded', timeout=35000)
            if response and response.status >= 400:
                raise ValueError('Institutioneller Dienst HTTP ' + str(response.status))
            selector = ('#id-ca-sidenav-logout,.profile-username,input[type=password]' if self.kind == 'tugrazonline'
                        else '#region-main,[data-region="myoverview"],[data-region="coursesync"],input[type=password]')
            try:
                await page.locator(selector).first.wait_for(state='attached', timeout=15000)
                if self.kind == 'tugrazonline':
                    await page.wait_for_load_state('networkidle', timeout=6000)
                else:
                    previous_height = 0
                    for _ in range(8):
                        height = await page.evaluate('document.body.scrollHeight')
                        await page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
                        await page.wait_for_timeout(250)
                        if height == previous_height:
                            break
                        previous_height = height
            except Exception:
                pass
            raw = (await page.content()).encode('utf-8')
            if session_signal(self.kind, raw, page.url) != 'authenticated':
                raise LoginRequired('Angemeldeter Inhalt nicht erkennbar; im eigenen sichtbaren Browser anmelden')
            return Fetched(page.url, raw, 'text/html', await page.title())
        finally:
            await page.close()

    async def discover_courses(self, url=None):
        url = url or SERVICE_URLS[self.kind]
        policy = CrawlPolicy(kind=self.kind)
        document = await self.fetch(url, url, policy)
        if self.kind == 'tugrazonline':
            soup = BeautifulSoup(document.raw, 'html.parser')
            link = next((node for node in soup.select('a[href]') if node.get_text(' ', strip=True).startswith('Meine Lehrveranstaltungen')), None)
            if link:
                target = urljoin(document.url, link['href'])
                document = await self.fetch(target, target, policy)
        return personal_course_links(document.raw, document.url, self.kind)

    async def export_calendar(self, url, start, end):
        """Explicit private download; never the calendar publication control."""
        url = safe_url(url)
        parts = urlsplit(url)
        if parts.hostname != 'online.tugraz.at' or parts.path != '/tug_online/wbKalender.wbExport':
            raise ValueError('Nur persönlich verlinkter Kalender-Download ist erlaubt')
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if end_date < start_date or (end_date - start_date).days > 366:
            raise ValueError('Kalenderzeitraum muss zwischen 0 und 366 Tagen liegen')
        start_text, end_text = start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y')
        page = await self.context.new_page()
        async def download_only(route):
            request = route.request
            if request.method == 'POST':
                target = urlsplit(request.url)
                values = parse_qs(request.post_data or '')
                if (target.hostname != parts.hostname or target.path != parts.path
                        or values.get('pDateFrom') != [start_text] or values.get('pDateTo') != [end_text]
                        or re.search(r'(?i)publish|veröffentlich|veroeffentlich', request.post_data or '')):
                    await route.abort()
                    return
                await route.continue_()
            else:
                await route.fallback()
        await page.route('**/*', download_only)
        try:
            response = await page.goto(url, wait_until='domcontentloaded', timeout=35000)
            if urlsplit(page.url).hostname != parts.hostname or (response and response.status >= 400):
                raise LoginRequired('Kalenderdownload verlangt eine eigene Anmeldung')
            await page.locator('input[name=pDateFrom]').wait_for(timeout=15000)
            await page.locator('input[name=pDateFrom]').fill(start_text)
            await page.locator('input[name=pDateTo]').fill(end_text)
            await page.locator('#pFormat_ICS').check()
            async with page.expect_download(timeout=35000) as pending:
                await page.locator('#idDownloadButton').click()
            download = await pending.value
            path = await download.path()
            if not path or Path(path).stat().st_size > 32 * 1024 * 1024:
                raise ValueError('Kalenderdownload fehlt oder über Dateigrenze')
            raw = Path(path).read_bytes()
            if not raw.lstrip().startswith(b'BEGIN:VCALENDAR'):
                raise ValueError('Kalenderdownload enthält keine ICS-Originaldaten')
            return Fetched(url, raw, 'text/calendar', 'Persönlicher Kalenderexport')
        finally:
            await page.close()

    async def close(self):
        await self.http.close()
