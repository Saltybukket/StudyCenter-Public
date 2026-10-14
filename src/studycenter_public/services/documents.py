import hashlib
import json
import re
import subprocess
import tempfile
import mimetypes
import os
import shutil
import sys
from uuid import uuid4
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from statistics import median

from bs4 import BeautifulSoup



def html_content(raw: bytes, url: str):
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    published = None
    for selector in ('meta[property="article:published_time"]', 'meta[name="date"]', 'time[datetime]'):
        node = soup.select_one(selector)
        if node:
            published = node.get("content") or node.get("datetime")
            break
    main = content_root(soup)
    for node in main.select("script,style,nav,footer,.site-header,form,input,button,.usermenu,.logininfo"):
        node.decompose()
    for node in main.select("s,del,strike,[style*='line-through']"):
        node.replace_with(" [DURCHGESTRICHEN: " + node.get_text(" ", strip=True) + "] ")
    # Preserve table rows and headers instead of flattening cell boundaries.
    for row in main.select("tr"):
        row.replace_with("\n" + " | ".join(cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)) + "\n")
    for heading in main.find_all(re.compile("^h[1-6]$")):
        heading.insert_before("\n" + "#" * int(heading.name[1]) + " ")
        heading.insert_after("\n")
    text = main.get_text("\n", strip=True)
    return title, published, [{"page": 1, "anchor": "html", "text": text, "gaps": []}]


def content_root(soup):
    # Some CMS themes place multiple content sections beside a short <main> title block.
    return soup.select_one('#region-main') or soup.select_one('#main') or soup.find('main') or soup.find('article') or soup.body or soup


def calendar_content(raw):
    # RFC 5545 line folding is transport syntax, not part of source field values.
    lines = []
    for line in raw.decode('utf-8-sig').replace('\r', '').split('\n'):
        if not line:
            continue
        if line.startswith((' ', '\t')) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    pages, event = [], None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            event = [line]
        elif event is not None:
            event.append(line)
            if line == 'END:VEVENT':
                uid = next((x[4:] for x in event if x.startswith('UID:')), None)
                if not uid:
                    raise ValueError('Kalendertermin ohne UID')
                pages.append({'page': len(pages)+1, 'anchor': uid, 'text': '\n'.join(event), 'gaps': []})
                event = None
    if event is not None or not pages:
        raise ValueError('Kalenderexport unvollständig oder ohne Termine')
    return pages


def ocr_pixmap(pixmap, settings):
    if not settings.tesseract or not Path(settings.tesseract).exists():
        return None, "OCR nötig; Tesseract in dieser Installation konfigurieren."
    with tempfile.TemporaryDirectory(dir=settings.temp_directory) as directory:
        png = Path(directory) / "page.png"
        pixmap.save(png)
        try:
            result = subprocess.run([settings.tesseract, str(png), "stdout", "-l", settings.ocr_lang], capture_output=True, timeout=120, encoding="utf-8", errors="replace", env=settings.process_env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                return None, f"OCR fehlgeschlagen; Sprachdaten {settings.ocr_lang} prüfen."
            if not result.stdout.strip():
                return None, "OCR hat keinen lesbaren Text gefunden; Seitenansicht prüfen."
            return result.stdout.strip(), None
        except (OSError, subprocess.TimeoutExpired):
            return None, "OCR konnte nicht ausgeführt werden."


def paper_grid(page, drawings, *, explicit=False):
    """Identify background paths, never erase page content or table/ink paths.

    A grid needs two dense regular families spanning most of the page, with
    mostly empty tiny cells. Regular, populated tables alone do not qualify.
    Automatic use additionally requires a pale stroke (as in OneNote exports).
    """
    families = defaultdict(lambda: [[], []])
    for index, drawing in enumerate(drawings):
        if drawing['type'] != 's' or len(drawing['items']) != 1:
            continue
        entry = drawing['items'][0]
        if entry[0] != 'l':
            continue
        a, b = entry[1:3]
        color = drawing.get('color') or (0, 0, 0)
        if not explicit and min(color) < .7:
            continue
        style = (tuple(round(c, 3) for c in color), round(drawing.get('width') or 0, 2), drawing.get('dashes'))
        if abs(a.y-b.y) < .2 and abs(a.x-b.x) >= page.rect.width*.75:
            families[style][0].append((a.y, index))
        elif abs(a.x-b.x) < .2 and abs(a.y-b.y) >= page.rect.height*.75:
            families[style][1].append((a.x, index))
    words = page.get_text('words')
    for horizontal, vertical in families.values():
        if min(len(horizontal), len(vertical)) < 16:
            continue
        axes, steps = [], []
        for lines, dimension in ((horizontal, page.rect.height), (vertical, page.rect.width)):
            positions = sorted(set(round(position, 2) for position, _ in lines))
            distances = [b-a for a, b in zip(positions, positions[1:])]
            step = median(distances)
            if step <= 0 or positions[-1]-positions[0] < dimension*.65:
                break
            if sum(abs(d-step) <= step*.08 for d in distances) < len(distances)*.95:
                break
            axes.append(positions)
            steps.append(step)
        if len(axes) != 2 or max(steps)/min(steps) > 1.25:
            continue
        # Text crosses many paper cells; a real table normally populates cells.
        cells = (len(axes[0])-1)*(len(axes[1])-1)
        if words and (len(words)/cells > .12 or median(w[3]-w[1] for w in words) < max(steps)*.7):
            continue
        indices = {i for _, i in horizontal+vertical}
        return indices, {'lines': len(indices), 'spacing': [round(s, 2) for s in steps],
                         'reason': 'Dichtes, gleichmäßiges Raster über fast die ganze Seite; überwiegend leere Zellen. Nur diese Hintergrundlinien werden bei Tabellen und Durchstreichungen ausgelassen.'}
    return set(), None


def cached_ocr(page):
    if 'ocr_text' in page:
        return page['ocr_text'], page.get('ocr_gaps', [])
    # Older stored pages have OCR before table/drawing notices. Keep this output
    # during a local geometry correction rather than running OCR again.
    text = page['text']
    visual = page.get('visual_transcription', {}).get('text')
    if visual and text.endswith(visual):
        text = text[:-len(visual)]
    match = re.search(r'\n\[OCR (?:ganze Seite|Bildbereich)', text)
    ocr = re.split(r'\n\[(?:Tabelle|DURCHGESTRICHEN|Mögliche Durchstreichung)', text[match.start():], maxsplit=1)[0] if match else ''
    return ocr, [g for g in page.get('gaps', []) if 'OCR' in g or 'Bildbereich' in g]


def read_pdf_page(page, settings, *, ignore_grid=None, previous=None):
    import pymupdf
    if page.rect.width * page.rect.height * (180 / 72) ** 2 > 48_000_000:
        raise ValueError('PDF-Seite überschreitet die sichere Rastergröße')
    text = page.get_text('text', sort=True)
    ocr_text, gaps = '', []
    if previous is not None:
        ocr_text, gaps = cached_ocr(previous)
    elif len(text.strip()) < 30 and (page.get_image_info() or page.get_drawings()):
        ocr, gap = ocr_pixmap(page.get_pixmap(dpi=180), settings)
        if ocr:
            ocr_text = '\n[OCR ganze Seite]\n'+ocr
        if gap:
            gaps.append(gap)
    else:
        seen = set()
        for info in page.get_image_info():
            rect = pymupdf.Rect(info['bbox']) & page.rect
            if rect.width < 60 or rect.height < 25 or tuple(rect) in seen:
                continue
            seen.add(tuple(rect))
            ocr, gap = ocr_pixmap(page.get_pixmap(dpi=180, clip=rect), settings)
            location = ','.join(str(round(x)) for x in rect)
            if ocr:
                ocr_text += f'\n[OCR Bildbereich {location}]\n{ocr}'
            if gap:
                gaps.append(f'Bildbereich {location}: {gap}')
    text += ocr_text
    drawings = page.get_drawings()
    excluded, grid = paper_grid(page, drawings, explicit=ignore_grid is True)
    if ignore_grid is False:
        excluded = set()
    paths = [drawing for i, drawing in enumerate(drawings) if i not in excluded]
    for table in page.find_tables(paths=paths).tables:
        text += '\n[Tabelle]\n'+'\n'.join(' | '.join(str(cell or '') for cell in row) for row in table.extract())
    for annotation in page.annots() or []:
        if annotation.type[0] == pymupdf.PDF_ANNOT_STRIKE_OUT:
            text += '\n[DURCHGESTRICHEN: '+page.get_textbox(annotation.rect)+']'
    words = page.get_text('words')
    for drawing in paths:
        for entry in drawing['items']:
            if entry[0] == 'l':
                start, end = entry[1:3]
                if abs(start.y-end.y) < 2 and abs(start.x-end.x) > 10:
                    strike = [word for x0,y0,x1,y1,word,*_ in words
                              if min(start.x,end.x) <= (x0+x1)/2 <= max(start.x,end.x)
                              and y0+(y1-y0)*.25 < start.y < y0+(y1-y0)*.75]
                    if strike:
                        text += '\n[Mögliche Durchstreichung: '+' '.join(strike)+'; Seitenansicht beachten]'
    if not text.strip():
        gaps.append('Kein lesbarer Seiteninhalt; Seitenansicht prüfen.')
    result = {'page': page.number+1, 'anchor': f'page-{page.number+1}', 'text': text, 'gaps': gaps,
              'ocr_text': ocr_text, 'ocr_gaps': list(gaps),
              'reading_options': {'ignore_grid': ignore_grid},
              'grid_filter': {**(grid or {}), 'applied': bool(excluded)}}
    if previous and previous.get('visual_transcription'):
        result['visual_transcription'] = previous['visual_transcription']
        result['text'] += '\n'+previous['visual_transcription']['text']
    return result


def pdf_content(path: Path, settings, *, page_options=None):
    import pymupdf
    pages = []
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF ist passwortgeschützt")
        if len(pdf) > 2000:
            raise ValueError('PDF überschreitet die Seitengrenze; keine Seiten werden still abgeschnitten')
        for number, page in enumerate(pdf, 1):
            pages.append(read_pdf_page(page, settings, ignore_grid=(page_options or {}).get(number)))
    return pages


@dataclass
class OCRSettings:
    tesseract: str | None = None
    ocr_lang: str = 'eng'
    temp_directory: str | None = None
    process_env: dict | None = None


def default_ocr(paths):
    from ..platform.components import component_root
    root = component_root(paths) / 'ocr'
    executable = root / ('tesseract.exe' if os.name == 'nt' else 'usr/bin/tesseract')
    environment = dict(os.environ)
    environment['TESSDATA_PREFIX'] = str(root / ('tessdata' if os.name == 'nt' else 'usr/share/tesseract-ocr/5/tessdata'))
    if os.name != 'nt':
        environment['LD_LIBRARY_PATH'] = str(root / 'usr/lib/x86_64-linux-gnu')
    return OCRSettings(str(executable) if executable.is_file() else None, 'eng+deu', process_env=environment)


class DocumentService:
    """Immutable original bytes and complete, version-bound readable pages."""

    def __init__(self, db, paths, *, ocr=None, max_bytes=64 * 1024 * 1024):
        self.db, self.paths, self.max_bytes = db, paths, max_bytes
        self.ocr = ocr or default_ocr(paths)
        ocr_cache = Path(paths.cache) / 'ocr'
        ocr_cache.mkdir(parents=True, exist_ok=True)
        self.ocr.temp_directory = str(ocr_cache)
        self._images = None
        with db.connection() as connection:
            connection.execute('CREATE VIRTUAL TABLE IF NOT EXISTS document_text_index USING fts5(document_id UNINDEXED, page UNINDEXED, text)')
            connection.execute('INSERT INTO document_text_index(document_id,page,text) SELECT document_id,page,text FROM pages p WHERE NOT EXISTS(SELECT 1 FROM document_text_index f WHERE CAST(f.document_id AS INTEGER)=p.document_id AND CAST(f.page AS INTEGER)=p.page)')

    def import_file(self, path, **kwargs):
        path = Path(path)
        if path.stat().st_size > self.max_bytes:
            raise ValueError('Dokument überschreitet die konfigurierte Dateigrenze')
        kwargs.setdefault('title', path.name)
        kwargs.setdefault('mime', mimetypes.guess_type(path.name)[0] or '')
        return self.import_bytes(path.read_bytes(), **kwargs)

    def import_bytes(self, data, *, title, source_url='', mime='', source_id=None, trusted_course_id=None, replaces_document_id=None):
        if not isinstance(data, bytes) or not data:
            raise ValueError('Dokument enthält keine Originalbytes')
        if len(data) > self.max_bytes:
            raise ValueError('Dokument überschreitet die konfigurierte Dateigrenze')
        if replaces_document_id is not None:
            previous = self.db.document(int(replaces_document_id))
            if not previous:
                raise KeyError('Zu ersetzendes Dokument nicht gefunden')
            if source_url and source_url != previous['url']:
                raise ValueError('Explizite Dokumentrevision darf die Quellidentität nicht wechseln')
            source_url, source_id, trusted_course_id = previous['url'], previous['source_id'], previous.get('trusted_course_id')
        if trusted_course_id is not None:
            course = self.db.course(trusted_course_id)
            if type(trusted_course_id) is not int or not course or course['source_id'] != source_id:
                raise ValueError('Dokumentkurs gehört nicht zur gewählten Quelle')
        source_url = source_url or 'urn:import:' + str(uuid4())
        digest = hashlib.sha256(data).hexdigest()
        versions = [doc for doc in self.db.documents(source_id=source_id)
                    if (doc.get('url') or doc.get('source_url') or '') == source_url
                    and doc.get('source_id') == source_id and doc.get('trusted_course_id') == trusted_course_id]
        latest = max(versions, key=lambda doc:doc['id'], default=None)
        if latest and latest.get('hash') == digest:
            return latest
        if data.startswith(b'%PDF-'):
            mime, suffix = 'application/pdf', '.pdf'
        elif 'html' in mime or data.lstrip().lower().startswith((b'<!doctype html', b'<html', b'<main')):
            mime, suffix = 'text/html', '.html'
        elif mime in ('message/rfc822',):
            suffix = '.eml'
        elif mime == 'text/calendar' or data.lstrip().startswith(b'BEGIN:VCALENDAR'):
            mime, suffix = 'text/calendar', '.ics'
        elif mime.startswith('image/'):
            suffix = mimetypes.guess_extension(mime) or '.img'
        elif mime.startswith('text/') or not mime:
            mime, suffix = mime or 'text/plain', '.txt'
        else:
            raise ValueError('Dokumentformat nicht unterstützt: ' + mime)
        directory = Path(self.paths.documents)
        directory.mkdir(parents=True, exist_ok=True)
        original = directory / (digest + suffix)
        if original.exists() and hashlib.sha256(original.read_bytes()).hexdigest() != digest:
            raise ValueError('Vorhandenes Original verletzt seinen Hash; beschädigte Datei wird nicht überschrieben')
        if not original.exists():
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            try:
                temporary.replace(original)
                if os.name != 'nt':
                    original.chmod(0o600)
            finally:
                temporary.unlink(missing_ok=True)
        published = None
        if mime == 'application/pdf':
            pages = pdf_content(original, self.ocr)
        elif mime == 'text/html':
            inferred_title, published, pages = html_content(data, source_url)
            title = title or inferred_title
        elif mime == 'text/calendar':
            pages = calendar_content(data)
        elif mime == 'message/rfc822':
            from ..collectors.mail import message_pages
            from email import policy
            from email.parser import BytesParser
            from email.utils import parsedate_to_datetime
            pages = message_pages(data)
            message = BytesParser(policy=policy.default).parsebytes(data)
            try:
                sent = parsedate_to_datetime(str(message.get('Date', '')))
                published = sent.isoformat() if sent.tzinfo else None
            except (TypeError, ValueError):
                published = None
        elif mime.startswith('image/'):
            import pymupdf
            with pymupdf.open(stream=data) as image:
                converted = image.convert_to_pdf()
            with pymupdf.open(stream=converted, filetype='pdf') as pdf:
                pages = [read_pdf_page(pdf[0], self.ocr)]
        else:
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('Textkodierung unbekannt; als UTF-8 importieren') from None
            pages = [{'page': 1, 'text': text, 'gaps': []}]
        if not pages:
            raise ValueError('Dokument enthält keine Seiten')
        revision = max((int(doc.get('revision') or doc.get('version') or 1) for doc in versions), default=0) + 1
        page_rows = [dict(page, metadata={key: value for key, value in page.items() if key not in ('page', 'text')}) for page in pages]
        record = self.db.save_document({'source_id': source_id, 'url': source_url, 'title': title,
            'trusted_course_id':trusted_course_id,
            'hash': digest, 'revision': revision, 'version': revision,
            'fetched_at': datetime.now(timezone.utc).isoformat(), 'published_at': published,
            'mime': mime, 'original_path': str(original), 'byte_count': len(data),
            'force_revision':bool(latest),
            'gaps': [gap for page in pages for gap in page.get('gaps', [])]}, pages=page_rows)
        with self.db.connection() as connection:
            connection.execute('DELETE FROM document_text_index WHERE document_id=?', (record['id'],))
            connection.executemany('INSERT INTO document_text_index(document_id,page,text) VALUES(?,?,?)',
                [(record['id'], page['page'], page['text']) for page in pages])
        return record

    def list_documents(self, source_id=None):
        return self.db.documents(source_id=source_id)

    def source_text(self, document_id, page=None):
        pages = self.db.pages(document_id)
        if page is not None:
            pages = [entry for entry in pages if entry['page'] == int(page)]
        if not pages:
            raise KeyError('Dokument oder Seite nicht gefunden')
        return '\n\n'.join(entry['text'] for entry in pages)

    def excluded_for_analysis(self, document_id):
        document = self.db.document(document_id)
        if not document:
            raise KeyError('Dokument nicht gefunden')
        source = self.db.source(document['source_id']) if document.get('source_id') else None
        return bool(source and document['url'] in source.get('options', {}).get('excluded_urls', []))

    def analysis_text(self, document_id, page=None):
        if self.excluded_for_analysis(document_id):
            raise ValueError('Dokument wurde ausdrücklich von der Auswertung ausgeschlossen')
        return self.source_text(document_id, page)

    def page(self, document_id, page):
        return next((entry for entry in self.db.pages(document_id) if entry['page'] == int(page)), None)

    def original(self, document_id):
        document = self.db.document(document_id)
        if not document:
            raise KeyError('Dokument nicht gefunden')
        path = Path(document.get('original_path') or document.get('path') or '').resolve()
        if not path.is_relative_to(Path(self.paths.documents).resolve()) or not path.is_file():
            raise ValueError('Original fehlt oder liegt außerhalb dieser Installation')
        if hashlib.sha256(path.read_bytes()).hexdigest() != document['hash']:
            raise ValueError('Originalhash stimmt nicht mehr überein')
        return path

    def search(self, query, limit=50):
        # Treat a user's words as literal phrases, never as raw FTS syntax.
        terms = re.findall(r'\w+', query, re.UNICODE)
        if not terms:
            return []
        expression = ' AND '.join('"' + term + '"' for term in terms)
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT CAST(document_id AS INTEGER) document_id, CAST(page AS INTEGER) page, snippet(document_text_index,2,\'\',\'\',\' … \',24) snippet FROM document_text_index WHERE document_text_index MATCH ? LIMIT ?',
                (expression, max(1, min(int(limit), 100))))]

    def page_image(self, document_id, page):
        import pymupdf
        original = self.original(document_id)
        with pymupdf.open(original) as pdf:
            if int(page) < 1 or int(page) > len(pdf):
                raise KeyError('Seite nicht gefunden')
            return pdf[int(page)-1].get_pixmap(dpi=125).tobytes('png')

    def reread_page(self, document_id, page, ignore_grid=None):
        """Create a new reading revision; previous pages and quotes stay intact."""
        import pymupdf
        document = self.db.document(document_id)
        original = self.original(document_id)
        if document['mime'] != 'application/pdf':
            raise ValueError('Erneutes Seitenlesen ist für PDFs verfügbar')
        page = int(page)
        retained = self.db.pages(document_id)
        previous = next((entry for entry in retained if entry['page'] == page), None)
        if not previous:
            raise KeyError('Seite nicht gefunden')
        with pymupdf.open(original) as pdf:
            replacement = read_pdf_page(pdf[page-1], self.ocr, ignore_grid=ignore_grid)
        pages = []
        for entry in retained:
            if entry['page'] == page:
                pages.append({'page':page, 'text':replacement['text'], 'metadata':{key:value for key,value in replacement.items() if key not in ('page','text')}})
            else:
                pages.append({'page':entry['page'], 'text':entry['text'], 'metadata':entry['metadata']})
        inherited = {key:document[key] for key in ('source_id','url','title','hash','published_at','mime','original_path','byte_count','trusted_course_id','course_title') if key in document}
        record = self.db.save_document({**inherited, 'force_revision':True,
            'fetched_at':datetime.now(timezone.utc).isoformat(), 'reading_parent':document_id,
            'gaps':[gap for entry in pages for gap in entry['metadata'].get('gaps', [])]}, pages=pages)
        with self.db.connection() as connection:
            connection.executemany('INSERT INTO document_text_index(document_id,page,text) VALUES(?,?,?)',
                [(record['id'], entry['page'], entry['text']) for entry in pages])
        return record
