"""Bounded, manual and read-only HTTP source traversal."""
import asyncio
import ipaddress
import socket
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

from ..services.documents import content_root


class UnsafeURL(ValueError):
    pass


class LoginRequired(RuntimeError):
    pass


MEDIA_SUFFIXES = ('.mp4', '.webm', '.mp3', '.ogg', '.wav', '.avi', '.mov', '.zip', '.gz', '.tar', '.exe', '.msi', '.iso', '.7z', '.woff', '.css', '.js')
MUTATIONS = ('logout', 'logoff', 'delete', 'remove', 'enrol', 'unenrol', 'edit.php', 'sesskey=', 'submit', 'attempt.php', 'processattempt', 'completion', 'markread', 'compose', 'preference', 'abmelden')


def mutating_url(url):
    for _ in range(3):
        decoded = unquote(url)
        if decoded == url:
            break
        url = decoded
    return any(word in url.casefold() for word in MUTATIONS)


def safe_url(url, *, preserve_fragment=False):
    parts = urlsplit(str(url))
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise UnsafeURL('Nur HTTP(S)-URLs ohne Zugangsdaten sind erlaubt')
    if parts.port not in (None, 80, 443):
        raise UnsafeURL('Nur Standard-Webports sind erlaubt')
    if any(char in url for char in ('\r', '\n', '\x00', '\\')):
        raise UnsafeURL('Ungültige URL')
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, parts.fragment if preserve_fragment else ''))


def resolve_public(host):
    return list(dict.fromkeys(result[4][0] for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))


def validate_addresses(addresses):
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeURL('Lokale, private und reservierte Netzwerkadressen sind ausgeschlossen')
    return addresses[0]


@dataclass
class CrawlPolicy:
    max_pages: int = 60
    max_depth: int = 4
    max_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    timeout: float = 35
    excluded_urls: list[str] = field(default_factory=list)
    scope: str | None = None
    kind: str = 'web'
    exclude_archives: bool = False
    forum_since: str | None = None

    def __post_init__(self):
        if not 1 <= self.max_pages <= 500 or not 0 <= self.max_depth <= 10:
            raise ValueError('Seitengrenze oder Tiefe ungültig')
        if not 1 <= self.max_bytes <= 64 * 1024 * 1024 or not self.max_bytes <= self.max_total_bytes <= 1024 * 1024 * 1024:
            raise ValueError('Datei- oder Gesamtgrenze ungültig')

    def allows(self, url, root):
        try:
            url, root = safe_url(url, preserve_fragment=self.kind == 'tugrazonline'), safe_url(root, preserve_fragment=self.kind == 'tugrazonline')
        except ValueError:
            return False
        target, origin = urlsplit(url), urlsplit(root)
        if target.netloc.lower() != origin.netloc.lower() or target.scheme != origin.scheme:
            return False
        if url in self.excluded_urls or mutating_url(url):
            return False
        if target.path.lower().endswith(MEDIA_SUFFIXES):
            return False
        if self.kind == 'teachcenter':
            if '/course/view.php' in target.path:
                return parse_qs(target.query).get('id') == parse_qs(origin.query).get('id')
            return url == root or '/pluginfile.php/' in target.path or target.path.endswith('/course/section.php') or any('/mod/' + name + '/' in target.path for name in ('page', 'resource', 'folder', 'book', 'forum', 'url', 'assign', 'quiz')) and target.path.endswith(('view.php', 'discuss.php'))
        if self.kind == 'tugrazonline':
            if url == root:
                return True
            root_ids = re.findall(r'/(?:student/courses|course)/(\d+)', origin.path + '#' + origin.fragment)
            target_ids = re.findall(r'/(?:student/courses|course)/(\d+)', target.path + '#' + target.fragment)
            if root_ids and target_ids and set(target_ids) != set(root_ids):
                return False
            route = target.path + '#' + target.fragment
            return bool(target_ids and root_ids and set(target_ids) == set(root_ids)
                        or any(path in route for path in ('wbLv.wbShowLVDetail', 'wbLv.wbShowLVElements', 'wbLv.wbShowLVInfos', 'wbKalender.wbExport')))
        prefix = self.scope or origin.path.rsplit('/', 1)[0] + '/'
        return target.path.startswith(prefix) or target.path.lower().endswith(('.pdf', '.ics'))


@dataclass
class Fetched:
    url: str
    raw: bytes
    mime: str
    title: str


@dataclass
class CollectorResult:
    authenticated: bool | None = None
    reachable: bool = False
    complete: bool = False
    changed: bool = False
    documents: list[Fetched] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    pending: list[tuple[str, int]] = field(default_factory=list)
    visited: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    cancelled: bool = False


def mime_type(content_type, raw, url):
    value = content_type.split(';')[0].strip().lower()
    if raw.startswith(b'%PDF-'):
        return 'application/pdf'
    if 'html' in value or raw.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        return 'text/html'
    if value.startswith('text/') or value.startswith('image/'):
        return value
    if raw.startswith(b'BEGIN:VCALENDAR'):
        return 'text/calendar'
    raise ValueError('Nicht unterstützter Dateityp: ' + (value or urlsplit(url).path))


def links_from(raw, url, root, policy):
    main = content_root(BeautifulSoup(raw, 'html.parser'))
    if policy.exclude_archives:
        for heading in list(main.select('.sectionname')):
            if heading.get_text(' ', strip=True).casefold().startswith('archiv'):
                section = heading.find_parent(class_='course-section')
                if section:
                    section.decompose()
    result, excluded = [], []
    for node in main.select('a[href],iframe[src],object[data],embed[src]'):
        locator = node.get('href') or node.get('src') or node.get('data')
        try:
            target = safe_url(urljoin(url, locator), preserve_fragment=policy.kind == 'tugrazonline')
        except ValueError:
            continue
        if target == url:
            continue
        if policy.allows(target, root):
            if target not in result:
                result.append(target)
        elif target not in excluded:
            excluded.append(target)
    return result, excluded


class PublicCollector:
    def __init__(self, *, client=None, resolver=resolve_public):
        self.client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False, headers={'User-Agent': 'StudyCenter-Public/0.1 (manual read-only reader)'})
        self.resolver = resolver

    async def fetch(self, url, root, policy):
        for _ in range(6):
            url = safe_url(url)
            if not policy.allows(url, root):
                raise UnsafeURL('URL oder Weiterleitung verlässt den ausgewählten Quellbereich')
            parts = urlsplit(url)
            addresses = await asyncio.to_thread(self.resolver, parts.hostname)
            address = validate_addresses(addresses)
            # Connect to the validated IP; Host and TLS SNI retain the original
            # origin. A second DNS lookup cannot change the connection target.
            host = parts.hostname.encode('idna').decode()
            target_host = '[' + address + ']' if ':' in address else address
            target = urlunsplit((parts.scheme, target_host, parts.path, parts.query, ''))
            async with self.client.stream('GET', target, headers={'Host': parts.netloc}, extensions={'sni_hostname': host}, timeout=policy.timeout) as response:
                if response.is_redirect:
                    url = safe_url(urljoin(url, response.headers.get('location', '')))
                    continue
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').split(';')[0].lower()
                if content_type.startswith(('video/', 'audio/')):
                    raise ValueError('Audio- und Videomedien sind vor dem Download ausgeschlossen')
                if int(response.headers.get('content-length', '0')) > policy.max_bytes:
                    raise ValueError('Dateigrenze erreicht')
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > policy.max_bytes:
                        raise ValueError('Dateigrenze erreicht')
                data = bytes(raw)
                mime = mime_type(response.headers.get('content-type', ''), data, url)
                if mime == 'text/html' and BeautifulSoup(data, 'html.parser').select_one('input[type=password],form#login'):
                    raise LoginRequired('Quelle verlangt eine eigene Anmeldung')
                return Fetched(url, data, mime, parts.path.rsplit('/', 1)[-1] or 'Quellseite')
        raise UnsafeURL('Zu viele Weiterleitungen')

    async def close(self):
        await self.client.aclose()


async def collect(root, collector, *, policy=None, state=None, cancel=None, progress=None):
    policy = policy or CrawlPolicy()
    root = safe_url(root, preserve_fragment=policy.kind == 'tugrazonline')
    result = CollectorResult()
    queue = list((state or {}).get('pending') or [(root, 0)])
    visited = set((state or {}).get('visited') or [])
    total = 0
    attempts = 0
    while queue:
        if cancel and cancel.is_set():
            result.cancelled = True
            result.gaps.append('Synchronisierung abgebrochen; offene URLs können fortgesetzt werden')
            break
        if attempts >= policy.max_pages:
            result.gaps.append('Seitengrenze erreicht; offene URLs bleiben sichtbar')
            break
        url, depth = queue.pop(0)
        if url in visited:
            continue
        if depth > policy.max_depth:
            result.pending.append((url, depth))
            result.gaps.append('Tiefengrenze erreicht: ' + url)
            continue
        attempts += 1
        try:
            document = await collector.fetch(url, root, policy)
            result.reachable = True
            if total + len(document.raw) > policy.max_total_bytes:
                queue.insert(0, (url, depth))
                result.gaps.append('Gesamtgrößengrenze erreicht')
                break
            total += len(document.raw)
            result.documents.append(document)
            visited.add(url)
            if policy.kind in ('teachcenter', 'tugrazonline'):
                result.authenticated = True
            if document.mime == 'text/html':
                links, excluded = links_from(document.raw, document.url, root, policy)
                result.excluded.extend(excluded)
                if depth < policy.max_depth:
                    queue.extend((link, depth + 1) for link in links if link not in visited and link not in [entry[0] for entry in queue])
                elif links:
                    result.gaps.append('Tiefengrenze erreicht: ' + url)
                    result.pending.extend((link, depth+1) for link in links if link not in visited)
        except LoginRequired as error:
            result.reachable = True
            result.authenticated = False
            result.gaps.append(str(error))
            queue.insert(0, (url, depth))
            break
        except (ValueError, httpx.HTTPError, OSError) as error:
            if isinstance(error, httpx.HTTPStatusError):
                result.reachable = True
                message = 'HTTP ' + str(error.response.status_code)
                if error.response.status_code == 401:
                    result.authenticated = False
            elif isinstance(error, UnsafeURL):
                message = str(error)
            elif isinstance(error, ValueError):
                message = 'Quellinhalt nicht lesbar oder Dateigrenze erreicht'
            else:
                message = 'Dienst nicht erreichbar (' + type(error).__name__ + ')'
            result.gaps.append(message + ': ' + url)
            # A failed URL is retained for an explicit retry, not marked visited.
            result.pending.append((url, depth))
        if progress:
            await progress({'fetched': len(result.documents), 'bytes': total, 'pending': len(queue), 'url': url})
    result.pending.extend(queue)
    result.visited = sorted(visited)
    result.complete = not result.pending and not result.gaps
    return result
