"""Explicit manual sync with persistent progress, gaps and retry cursors."""
import asyncio
import hashlib
from dataclasses import fields
from datetime import datetime, timezone

from ..collectors.web import CrawlPolicy, PublicCollector, collect, safe_url, LoginRequired
from ..collectors.browser import BrowserManager, BrowserUnavailable, SERVICE_URLS
from ..collectors.tu import InstitutionalCollector
from ..collectors.mail import IMAPCollector, WebmailCollector, MailError
from .documents import DocumentService


class SyncService:
    def __init__(self, db, paths, browser=None, *, secrets=None, documents=None, collector_factory=None):
        self.db, self.paths = db, paths
        self.browser = browser or BrowserManager(paths)
        self.secrets = secrets
        self.documents = documents or DocumentService(db, paths)
        self.collector_factory = collector_factory
        self._cancel = {}
        self._running = set()

    def register_source(self, name, url='', kind='web', options=None, source_id=None):
        kind = {'public':'web', 'mail':'imap'}.get(kind, kind)
        if kind not in ('web', 'teachcenter', 'tugrazonline', 'imap', 'webmail'):
            raise ValueError('Quellenart nicht unterstützt')
        if not name.strip() or len(name) > 200:
            raise ValueError('Begrenzter Quellenname erforderlich')
        if kind != 'imap':
            url = safe_url(url or SERVICE_URLS.get(kind, ''), preserve_fragment=kind == 'tugrazonline')
        options = dict(options or {})
        if any(key in options for key in ('password','cookie','token','username')):
            raise ValueError('Zugangsdaten gehören ausschließlich in den sicheren lokalen Zugangsspeicher')
        allowed = {field.name for field in fields(CrawlPolicy)}
        CrawlPolicy(**{key:value for key,value in options.items() if key in allowed} | {'kind':kind})
        return self.db.upsert_source(source_id, name=name.strip(), kind=kind, metadata={'url':url, 'options':options})

    async def discover_courses(self, source_id):
        source = self._source(source_id)
        if source['kind'] not in ('teachcenter', 'tugrazonline'):
            raise ValueError('Kursentdeckung ist für die institutionellen Quellen verfügbar')
        async with self.browser.session() as context:
            collector = InstitutionalCollector(context, source['kind'])
            try:
                courses = await collector.discover_courses(source['url'])
            finally:
                await collector.close()
        self._update(source, {'discovered_courses': courses, 'discovered_at': self._now()})
        return courses

    def select_courses(self, source_id, courses):
        source = self._source(source_id)
        discovered = {str(course['external_id']):course for course in source.get('discovered_courses', [])}
        requested = [str(course.get('external_id')) if isinstance(course, dict) else str(course) for course in courses]
        if len(requested) > 100:
            raise ValueError('Höchstens 100 eigene Kurse pro Quelle auswählen')
        if any(identifier not in discovered for identifier in requested):
            raise ValueError('Nur tatsächlich entdeckte Kurse können ausgewählt werden')
        selected = []
        for identifier in dict.fromkeys(requested):
            course = discovered[identifier]
            local = self.db.upsert_course(source['id'], identifier, course['title'],
                metadata={key:course[key] for key in ('url','kind','course_type','group') if key in course})
            selected.append({**course, 'trusted_course_id':local['id']})
        return self._update(source, {'selected_courses': selected, 'crawl_states': {}})

    def status(self, source_id):
        source = self._source(source_id)
        return source.get('sync_status', {'state':'idle', 'complete':False, 'gaps':[]})

    def cancel(self, source_id):
        source_id = int(source_id)
        if source_id in self._cancel:
            self._cancel[source_id].set()
        return {'source_id':source_id, 'cancel_requested': source_id in self._running}

    def configure_mail(self, values):
        if not self.secrets:
            raise ValueError('Sicherer Zugangsspeicher nicht verbunden')
        IMAPCollector(self.secrets).configure(values)
        return self.mail_status()

    def mail_status(self):
        if not self.secrets:
            return {'configured':False}
        import json
        value = self.secrets.get('mail')
        values = json.loads(value) if isinstance(value, str) else value or {}
        return {key:values.get(key, '') for key in ('host','username')} | {'configured':bool(values.get('password'))}

    async def login(self, source_id):
        source = self._source(source_id)
        if source['kind'] not in ('teachcenter','tugrazonline','webmail'):
            raise ValueError('Diese Quelle verwendet keinen institutionellen Browserzugang')
        return await self.browser.login(source['url'], explicit=True, service=source['kind'])

    async def sync(self, source_id, resume=False):
        source_id = int(source_id)
        if source_id in self._running:
            raise ValueError('Diese Quelle wird bereits synchronisiert')
        source = self._source(source_id)
        if source['kind'] in ('teachcenter','tugrazonline') and not source.get('selected_courses'):
            raise ValueError('Zuerst eigene Kurse entdecken und ausdrücklich auswählen')
        self._running.add(source_id)
        cancellation = self._cancel[source_id] = asyncio.Event()
        started = self._now()
        status = {'state':'running', 'source_id':source_id, 'started_at':started,
                  'complete':False, 'authenticated':None, 'reachable':False,
                  'changed':False, 'fetched':0, 'imported':0, 'gaps':[], 'document_ids':[]}
        self._update(source, {'sync_status':status})
        try:
            if source['kind'] == 'imap':
                if not self.secrets:
                    raise LoginRequired('Sicherer Mailzugangsspeicher nicht verbunden')
                collector = IMAPCollector(self.secrets)
                result, state = await asyncio.to_thread(collector.collect, source.get('options', {}), state=source.get('mail_state') if resume else None, cancel=cancellation)
                self._import(source, result, status)
                if not result.pending:
                    self._update(source, {'mail_state':state})
                status.update(complete=result.complete and not status['gaps'], reachable=result.reachable, authenticated=result.authenticated)
            elif source['kind'] == 'webmail':
                async with self.browser.session() as context:
                    collector = WebmailCollector(context)
                    try:
                        result, state = await collector.collect(source.get('options', {}), state=source.get('mail_state') if resume else None, cancel=cancellation)
                        self._import(source, result, status)
                        if not result.pending:
                            self._update(source, {'mail_state':state})
                        status.update(complete=result.complete and not status['gaps'], reachable=result.reachable, authenticated=result.authenticated)
                    finally:
                        await collector.close()
            elif source['kind'] in ('teachcenter','tugrazonline'):
                async with self.browser.session() as context:
                    collector = InstitutionalCollector(context, source['kind'])
                    try:
                        await self._crawl(source, collector, status, resume, cancellation)
                        options = source.get('options', {})
                        if options.get('calendar_url') and not cancellation.is_set():
                            calendar = await collector.export_calendar(options['calendar_url'], options['calendar_start'], options['calendar_end'])
                            from ..collectors.web import CollectorResult
                            self._import(source, CollectorResult(documents=[calendar]), status)
                            status['complete'] = status['complete'] and not status['gaps']
                    finally:
                        await collector.close()
            else:
                collector = self.collector_factory() if self.collector_factory else PublicCollector()
                try:
                    await self._crawl(source, collector, status, resume, cancellation)
                finally:
                    await collector.close()
            status['state'] = 'cancelled' if cancellation.is_set() else 'login_required' if status['authenticated'] is False else 'complete' if status['complete'] else 'partial'
        except LoginRequired as error:
            status.update(state='login_required', authenticated=False, complete=False)
            status['gaps'].append(str(error))
        except (BrowserUnavailable, MailError, ValueError, OSError) as error:
            status.update(state='failed', complete=False)
            status['gaps'].append(str(error))
        except Exception as error:
            # No response bodies, tokens, passwords or executable source text enter
            # a user-facing error. Existing original records stay available.
            status.update(state='failed', complete=False)
            status['gaps'].append('Synchronisierung unterbrochen (' + type(error).__name__ + ')')
        finally:
            status['finished_at'] = self._now()
            self._update(source, {'sync_status':status})
            self._running.discard(source_id)
            self._cancel.pop(source_id, None)
        return status

    async def _crawl(self, source, collector, status, resume, cancellation):
        roots = []
        for course in source.get('selected_courses', []):
            trusted = self.db.course(course.get('trusted_course_id'))
            if not trusted or trusted['source_id'] != source['id'] or trusted['external_id'] != str(course['external_id']):
                raise ValueError('Gespeicherte Kursbindung fehlt; Kurse erneut ausdrücklich auswählen')
            roots.append((course['url'], trusted['id']))
        roots = roots or [(source['url'], None)]
        if source['kind'] == 'tugrazonline':
            from urllib.parse import urlsplit
            roots.extend((course['exam_url'], course['trusted_course_id']) for course in source.get('selected_courses', [])
                         if course.get('exam_url') and urlsplit(course['exam_url']).hostname == 'online.tugraz.at')
            roots = list(dict.fromkeys(roots))
        policies = {field.name for field in fields(CrawlPolicy)}
        options = source.get('options', {})
        policy = CrawlPolicy(**{key:value for key,value in options.items() if key in policies} | {'kind':source['kind']})
        states = source.get('crawl_states', {}) if resume else {}
        complete = True
        for root, trusted_course_id in roots:
            if cancellation.is_set():
                complete = False
                break
            async def progress(value):
                status['progress'] = value
                self._update(source, {'sync_status':status})
            state_key = str(trusted_course_id) + ':' + root if trusted_course_id is not None else root
            result = await collect(root, collector, policy=policy, state=states.get(state_key), cancel=cancellation, progress=progress)
            self._import(source, result, status, trusted_course_id=trusted_course_id)
            states[state_key] = {'pending':result.pending, 'visited':result.visited}
            self._update(source, {'crawl_states':states})
            status['reachable'] = status['reachable'] or result.reachable
            if result.authenticated is not None:
                status['authenticated'] = result.authenticated
            complete = complete and result.complete
        status['complete'] = complete and not status['gaps']

    def _import(self, source, result, status, trusted_course_id=None):
        status['fetched'] += len(result.documents)
        status['gaps'].extend(result.gaps)
        for document in result.documents:
            previous = {entry['id'] for entry in self.db.documents(source_id=source['id'])}
            try:
                record = self.documents.import_bytes(document.raw, title=document.title,
                    source_url=document.url, mime=document.mime, source_id=source['id'], trusted_course_id=trusted_course_id)
                status['document_ids'].append(record['id'])
                status['imported'] += 1
                status['changed'] = status['changed'] or record['id'] not in previous
                status['gaps'].extend(record.get('gaps', []))
                if document.mime == 'message/rfc822':
                    self._attachments(document, source, status, trusted_course_id=trusted_course_id)
            except (ValueError, OSError, RuntimeError) as error:
                status['gaps'].append('Dokument nicht vollständig lesbar: ' + document.url + ' (' + type(error).__name__ + ')')
                result.complete = False
                result.pending.append((document.url, 0))
                result.visited = [url for url in result.visited if url != document.url]

    def _attachments(self, document, source, status, trusted_course_id=None):
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(document.raw)
        for index, part in enumerate(message.walk()):
            if part.is_multipart() or not (part.get_filename() or part.get_content_disposition() == 'attachment'):
                continue
            mime = part.get_content_type()
            if mime in ('application/pkcs7-signature', 'application/pgp-signature'):
                continue
            raw = part.get_payload(decode=True)
            try:
                record = self.documents.import_bytes(raw, title=part.get_filename() or 'Mailanhang',
                    source_url=document.url + '#attachment-' + str(index), mime=mime, source_id=source['id'], trusted_course_id=trusted_course_id)
                status['document_ids'].append(record['id'])
                status['gaps'].extend(record.get('gaps', []))
            except (ValueError, OSError, RuntimeError):
                status['gaps'].append('Anhang nicht lesbar: ' + (part.get_filename() or mime))

    def _source(self, source_id):
        source = self.db.source(int(source_id))
        if not source:
            raise KeyError('Quelle nicht gefunden')
        return source

    def _update(self, source, values):
        # Merge with the current row so independent progress/state writes survive.
        latest = self._source(source['id'])
        updated = self.db.upsert_source(source['id'], name=latest['name'], kind=latest['kind'], metadata=values)
        source.update(updated)
        return updated

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()
