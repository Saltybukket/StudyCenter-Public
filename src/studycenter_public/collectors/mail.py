"""Optional own-account mail reads; never STORE, SMTP, CLOSE or read flags."""
import asyncio
import base64
import imaplib
import json
import re
import ssl
import socket
import hashlib
from datetime import date
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qs

import httpx
from bs4 import BeautifulSoup

from .web import Fetched, CollectorResult, LoginRequired, validate_addresses, resolve_public
from ..services.documents import html_content


class MailError(RuntimeError):
    pass


def message_pages(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = []
    def read_part(part):
        if part.get_content_disposition() == 'attachment' or part.get_filename():
            return
        if part.get_content_type() == 'multipart/alternative':
            children = list(part.iter_parts())
            if children:
                read_part(next((child for child in children if child.get_content_type() == 'text/plain'), children[-1]))
        elif part.is_multipart():
            for child in part.iter_parts():
                read_part(child)
        elif part.get_content_type() in ('text/plain', 'text/html'):
            text = part.get_content()
            if part.get_content_type() == 'text/html':
                text = html_content(text.encode(), 'mail')[2][0]['text']
            body.append(text)
    read_part(message)
    headers = '\n'.join(f'{field}: {message[field]}' for field in ('Subject', 'From', 'Date', 'Message-ID', 'In-Reply-To') if message[field])
    return [{'page': 1, 'text': headers + '\n\n' + '\n\n'.join(body),
             'gaps': [] if body else ['Nachricht enthält keinen lesbaren Text; Anhänge separat prüfen.']}]


def folder_name(value):
    if not value or any(char in value for char in ('\r', '\n', '\x00')):
        raise MailError('Mailordner ungültig')
    result, pending = [], []
    def flush():
        if pending:
            result.append('&' + base64.b64encode(''.join(pending).encode('utf-16-be')).decode().rstrip('=').replace('/', ',') + '-')
            pending.clear()
    for character in value:
        if ' ' <= character <= '~':
            flush()
            result.append('&-' if character == '&' else character)
        else:
            pending.append(character)
    flush()
    return '"' + ''.join(result).replace('\\', '\\\\').replace('"', '\\"') + '"'


def literal(result):
    return b''.join(part[1] for part in result if isinstance(part, tuple) and isinstance(part[1], bytes))


def matches(message, options):
    if options.get('include_all'):
        return True
    terms = [term for term in options.get('keywords', []) if term.strip()]
    sender = parseaddr(str(message.get('From', '')))[1].casefold()
    return sender in [item.casefold() for item in options.get('senders', [])] or any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', str(message.get('Subject', '')), re.I) for term in terms)


class IMAPCollector:
    def __init__(self, secrets, *, client_factory=None):
        self.secrets, self.client_factory = secrets, client_factory

    def configure(self, values):
        if not values.get('username') or not values.get('password'):
            raise ValueError('Eigener Mailbenutzername und Zugang erforderlich')
        self._host(values.get('host', ''))
        self.secrets.set('mail', json.dumps(values))

    @staticmethod
    def _host(host):
        host = host.strip().lower()
        if not re.fullmatch(r'[a-z0-9.-]+', host) or not (host == 'tugraz.at' or host.endswith('.tugraz.at')):
            raise MailError('TU-Mailhost aus eigener Kontokonfiguration erforderlich')
        return host

    def collect(self, options, *, state=None, cancel=None):
        value = self.secrets.get('mail')
        if not value:
            raise LoginRequired('Eigener Mailzugang fehlt; lokal konfigurieren')
        values = json.loads(value) if isinstance(value, str) else value
        host = self._host(values['host'])
        current = dict(state or {})
        result = CollectorResult()
        if not options.get('include_all') and not options.get('keywords') and not options.get('senders'):
            raise MailError('Konkreter Mailfilter oder ausdrückliche Auswahl aller Nachrichten erforderlich')
        since = date.fromisoformat(options.get('since', date.today().isoformat()))
        factory = self.client_factory
        if not self.client_factory:
            pinned_address = validate_addresses(resolve_public(host))
            class PinnedIMAP(imaplib.IMAP4_SSL):
                def _create_socket(self, timeout):
                    connection = socket.create_connection((pinned_address, 993), timeout)
                    return self.ssl_context.wrap_socket(connection, server_hostname=host)
            factory = lambda: PinnedIMAP(host, 993, ssl_context=ssl.create_default_context(), timeout=25)
        client = factory()
        try:
            client.login(values['username'], values['password'])
            kind, _ = client.select(folder_name(options.get('folder', 'INBOX')), readonly=True)
            if kind != 'OK':
                raise MailError('Mailordner nicht lesbar')
            _, validity = client.response('UIDVALIDITY')
            if not validity or not validity[0] or not validity[0].isdigit():
                raise MailError('UIDVALIDITY fehlt; kein unsicherer Cursor')
            uidvalidity = validity[0].decode()
            last = int(current.get('last_uid', 0)) if current.get('uidvalidity') == uidvalidity else 0
            months = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
            kind, found = client.uid('SEARCH', None, 'SINCE', f'{since.day:02}-{months[since.month-1]}-{since.year}', 'UID', f'{last+1}:*')
            if kind != 'OK':
                raise MailError('Mail-Suche fehlgeschlagen')
            uids = sorted(int(uid) for uid in b' '.join(item for item in found if isinstance(item, bytes)).split() if int(uid) > last)
            budget = max(1, min(200, int(options.get('max_pages', 30))))
            scanned = 0
            for uid in uids[:budget]:
                if cancel and cancel.is_set():
                    result.cancelled = True
                    break
                kind, envelope = client.uid('FETCH', str(uid), '(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])')
                header = literal(envelope)
                if kind != 'OK' or not header:
                    raise MailError('Nachrichtenkopf unvollständig; UID bleibt offen')
                message = BytesParser(policy=policy.default).parsebytes(header)
                identity = str(message.get('Message-ID') or f'{uidvalidity}:{uid}')
                url = f'imap://{host}/' + urlencode({'folder': options.get('folder', 'INBOX'), 'message': identity})
                if url not in options.get('excluded_urls', []) and matches(message, options):
                    size = re.search(rb'RFC822.SIZE\s+(\d+)', b' '.join(item[0] for item in envelope if isinstance(item, tuple)))
                    if not size or int(size[1]) > 32 * 1024 * 1024:
                        raise MailError('Mailgröße fehlt oder über Dateigrenze; UID bleibt offen')
                    kind, content = client.uid('FETCH', str(uid), '(BODY.PEEK[])')
                    raw = literal(content)
                    if kind != 'OK' or len(raw) != int(size[1]):
                        raise MailError('Nachricht unvollständig; UID bleibt offen')
                    result.documents.append(Fetched(url, raw, 'message/rfc822', str(message.get('Subject', 'Mail'))))
                last = uid
                scanned += 1
            result.reachable = result.authenticated = True
            result.complete = scanned == len(uids) and not result.cancelled
            current.update(uidvalidity=uidvalidity, last_uid=last)
            if not result.complete:
                result.gaps.append('Weitere Nachrichten ausstehend; manuell fortsetzen')
            return result, current
        except imaplib.IMAP4.error:
            raise LoginRequired('Mailzugang abgewiesen oder Sitzung unterbrochen') from None
        finally:
            client.logout()


class WebmailCollector:
    def __init__(self, context):
        self.context = context
        self.client = httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def _request(self, url, *, form=None, max_bytes=32 * 1024 * 1024):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'webmail.tugraz.at' or parts.username or parts.password or parts.port not in (None,443):
            raise MailError('Webmail-Endpunkt nicht freigegeben')
        query = parse_qs(parts.query)
        allowed = (parts.path.endswith('/services/ajax.php/imp/viewPort') and form is not None
                   and not parts.query and set(form) <= {'view', 'viewport', 'token'}
                   or parts.path == '/imp/dynamic.php' and form is None and query == {'page':['mailbox']}
                   or parts.path == '/imp/view.php' and form is None and query.get('actionID') == ['view_source']
                   and set(query) == {'actionID','mailbox','buid'} and len(query['buid']) == 1 and query['buid'][0].isdigit())
        if not allowed:
            raise MailError('Nur Webmail-Liste und unveränderte Originalnachricht sind freigegeben')
        if form and 'viewport' in form:
            viewport = json.loads(form['viewport'])
            if set(viewport) - {'view', 'qsearchmbox', 'qsearchfield', 'qsearch', 'slice'}:
                raise MailError('Mailboxänderung in Webmail-Anfrage abgewiesen')
        address = validate_addresses(await asyncio.to_thread(resolve_public, parts.hostname))
        target = urlunsplit(('https', '['+address+']' if ':' in address else address, parts.path, parts.query, ''))
        cookies = await self.context.cookies(url)
        headers = {'Host': parts.hostname, 'Cookie': '; '.join(cookie['name']+'='+cookie['value'] for cookie in cookies)}
        if form:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        async with self.client.stream('POST' if form else 'GET', target, data=form, headers=headers, extensions={'sni_hostname': parts.hostname}, timeout=35) as response:
            if response.status_code in (301,302,303,307,308,401,403):
                raise LoginRequired('Webmail-Sitzung fehlt oder ist abgelaufen')
            response.raise_for_status()
            raw = bytearray()
            async for block in response.aiter_bytes():
                raw.extend(block)
                if len(raw) > max_bytes:
                    raise MailError('Webmail-Dateigrenze erreicht')
            return bytes(raw), response.headers.get('content-type', '')

    async def collect(self, options, *, state=None, cancel=None):
        raw, _ = await self._request('https://webmail.tugraz.at/imp/dynamic.php?page=mailbox')
        soup = BeautifulSoup(raw, 'html.parser')
        javascript = '\n'.join(script.get_text() for script in soup.select('script:not([src])'))
        configs = {}
        for match in re.finditer(r'([A-Za-z_$][\w$.]*)\s*=\s*(?=\{)', javascript):
            try:
                value, _ = json.JSONDecoder().raw_decode(javascript[match.end():])
                if isinstance(value, dict):
                    configs[match[1]] = value
            except ValueError:
                continue
        core, imp = configs.get('HordeCore.conf', {}), configs.get('DimpCore.conf', {})
        if not core.get('TOKEN') or not imp.get('qsearchid'):
            raise LoginRequired('Webmail-Dynamic-Sitzung nicht erkennbar; sichtbar anmelden')
        endpoint = urljoin('https://webmail.tugraz.at/', core.get('URI_AJAX', '')) + 'viewPort'
        terms = [('subject', term) for term in options.get('keywords', [])] + [('from', term) for term in options.get('senders', [])]
        if options.get('include_all'):
            terms = [('from', '@')]
        if not 1 <= len(terms) <= 20:
            raise MailError('Ein bis zwanzig Mailfilter erforderlich')
        current = dict(state or {})
        query_index, offset = int(current.get('query_index', 0)), int(current.get('offset', 0))
        result = CollectorResult(reachable=True, authenticated=True)
        budget = max(1, min(200, int(options.get('max_pages', 30))))
        since = date.fromisoformat(options.get('since', date.today().isoformat()))
        processed = 0
        async def search(field, term, offset, size):
            viewport = {'view': imp['qsearchid'], 'qsearchmbox': base64.urlsafe_b64encode(options.get('folder', 'INBOX').encode()).decode().rstrip('='), 'qsearchfield': field, 'qsearch': term, 'slice': f'{offset+1}:{offset+size}'}
            data, _ = await self._request(endpoint, form={'view': imp['qsearchid'], 'viewport': json.dumps(viewport), 'token': core['TOKEN']})
            text = data.decode().strip()
            if text.startswith('/*') and text.endswith('*/'):
                text = text[2:-2].strip().removeprefix('-secure-').strip()
            payload = json.loads(text)
            if payload.get('reload') or payload.get('response') is False:
                raise LoginRequired('Webmail-Sitzung hat Leseanfrage abgewiesen')
            value = payload.get('tasks', {}).get('imp:viewport', {})
            records = value.get('data', {})
            if not isinstance(records, dict):
                if records == []:
                    records = {}
                else:
                    raise MailError('Webmail-Liste ohne eindeutige Nachrichtenkennung')
            return value, records
        while query_index < len(terms) and processed < budget:
            if cancel and cancel.is_set():
                result.cancelled = True
                break
            field, term = terms[query_index]
            size = min(20, budget - processed)
            viewport, records = await search(field, term, offset, size)
            total = int(viewport.get('totalrows', 0))
            if not records and offset < total:
                raise MailError('Webmail-Leselücke; Cursor bleibt offen')
            before = {key: value.get('flag', []) for key, value in records.items()}
            for key in records:
                if not str(key).isdigit():
                    raise MailError('Webmail-Nachrichtenkennung ungültig')
                url = 'https://webmail.tugraz.at/imp/view.php?' + urlencode({'actionID':'view_source', 'mailbox':imp['qsearchid'], 'buid':key})
                message_raw, mime = await self._request(url)
                if not message_raw or 'html' in mime:
                    raise MailError('Originalnachricht nicht vollständig abrufbar')
                message = BytesParser(policy=policy.default).parsebytes(message_raw)
                if not message['From'] or not message['Date']:
                    raise MailError('Mail ohne überprüfbare Quellkopfzeilen')
                try:
                    sent_date = parsedate_to_datetime(str(message['Date'])).date()
                except (ValueError, TypeError):
                    raise MailError('Mailzeitpunkt nicht überprüfbar') from None
                if sent_date >= since and matches(message, options):
                    # A session BUID is never used as permanent message identity.
                    identity = str(message.get('Message-ID') or hashlib.sha256(message_raw).hexdigest())
                    stable_url = 'urn:mail:' + hashlib.sha256((options.get('folder', 'INBOX') + ':' + identity).encode()).hexdigest()
                    result.documents.append(Fetched(stable_url, message_raw, 'message/rfc822', str(message.get('Subject', 'Mail'))))
            _, after = await search(field, term, offset, size)
            if before != {key: value.get('flag', []) for key, value in after.items()}:
                raise MailError('Mail-Liste oder Flags während Abruf verändert; Cursor bleibt offen')
            offset += len(records)
            processed += len(records)
            if offset >= total:
                query_index += 1
                offset = 0
        result.complete = query_index == len(terms) and not result.cancelled
        current.update(query_index=query_index, offset=offset)
        if not result.complete:
            result.gaps.append('Weitere Mail-Suchtreffer ausstehend; manuell fortsetzen')
        return result, current

    async def close(self):
        await self.client.aclose()
