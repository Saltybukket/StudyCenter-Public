"""Bounded, local study-material retrieval for explicitly requested quest plans."""
import hashlib
import json
import re
from collections import Counter
from urllib.parse import unquote, urlsplit
from pathlib import Path

from .db import encode
from .documents import reading_id
from .source_validity import _SEMESTER

STOP = set('ich möchte will lernen üben verstehen bitte das die der den dem ein eine einen und oder für von mit zum zur im in am auf bearbeiten vorbereiten einreichen aufgabe arbeitsblatt quiz abgabe lernen'.split())


def current_document(db, document_id, con=None):
    row = db.one('''SELECT d.id,d.title,d.hash,d.revision,d.pages,d.url,d.source_id,
        c.title course,c.id course_id,c.semester FROM documents d JOIN sources s ON s.id=d.source_id
        JOIN courses c ON c.id=s.course_id WHERE d.id=? AND s.active=1 AND c.active=1
        AND (s.kind IN ('teachcenter','public','gitlab') OR (s.kind='manual' AND d.mime='application/pdf'))
        AND d.mime IN ('application/pdf','text/plain','text/html','text/markdown') AND d.analysis_state!='archived'
        AND NOT EXISTS(SELECT 1 FROM documents n WHERE n.source_id=d.source_id AND n.url=d.url
          AND (n.fetched_at>d.fetched_at OR (n.fetched_at=d.fetched_at AND n.id>d.id)))''',
        (document_id,), connection=con)
    if not row or not document_model_allowed(db,document_id,con):
        return None
    pages = json.loads(row['pages'])
    labels = set()
    for match in _SEMESTER.finditer(row['title']+' '+' '.join((p.get('text') or '')[:1000] for p in pages[:2])):
        kind, year, _ = match.groups()
        labels.add(year+('W' if kind.casefold() in ('ws','wintersemester') else 'S'))
    if re.fullmatch(r'20\d{2}[WS]', row['semester'] or '') and len(labels)==1 and next(iter(labels)) < row['semester']:
        return None
    row['pages'] = pages
    return row


def collect(db, goal, items, bound_documents, index):
    """Return text and a server-owned allowlist; model URLs are never accepted."""
    context, allowed, seen = [], {}, set()
    docs = {}

    def add(document_id, page, text, gaps=(), truncated=False, original_only=False):
        key = (document_id, page)
        if key in seen or not isinstance(page, int) or not text.strip():
            return
        if document_id not in docs:
            docs[document_id] = current_document(db, document_id)
        doc = docs[document_id]
        if not doc:
            return
        seen.add(key)
        stored_page = next((p for p in doc['pages'] if p.get('page')==page), None)
        # Preserve the exact reader page version; missing extracted pages cannot
        # be linked as if the reader already contained the text.
        if not stored_page and not original_only:
            return
        allowed[key] = {'document_id':document_id, 'page':page, 'title':unquote(doc['title']),
            'course':doc['course'], 'course_id':doc['course_id'], 'hash':doc['hash'], 'revision':doc['revision'],
            'page_hash':hashlib.sha256(encode(stored_page).encode()).hexdigest() if stored_page else None,
            'original_only':not bool(stored_page),
            'reading_id':reading_id(stored_page) if stored_page else '',
            'url':f'/documents/{document_id}?page={page}&reading={reading_id(stored_page)}' if stored_page else f'/documents/{document_id}/pages/{page}.png'}
        context.append({'document_id':document_id, 'page':page, 'title':unquote(doc['title']),
            'course':doc['course'], 'text':text[:4000], 'gaps':list(gaps),
            'truncated':truncated or len(text)>4000})
        return True

    # Actual assignment pages take priority; the existing reader budgets and
    # warnings continue to describe full worksheet coverage.
    for doc in bound_documents:
        for page in doc['pages']:
            add(doc['document_id'], page.get('page'), page.get('text') or '', page.get('gaps',[]), page.get('truncated',False), original_only=True)
    query_text = goal or ' '.join(i['data'].get('title','') for i in items)
    terms = list(dict.fromkeys(t for t in re.findall(r'[^\W_]+', query_text.casefold()) if len(t)>2 and t not in STOP and not t.isdigit()))[:6]
    if items:
        words=Counter(t for d in bound_documents for p in d['pages'] for t in re.findall(r'[^\W_]+', (p.get('text') or '').casefold()) if len(t)>6 and t not in STOP)
        terms=list(dict.fromkeys(terms+[word for word,_ in words.most_common(6)]))[:10]
    if 'opv' in terms:
        terms += ['Operationsverstärker', 'OpAmp']
    hits = {}
    courses = sorted({i['course_id'] for i in items if i.get('course_id')}) if items else ['']
    for term in terms:
        for course in courses:
            for hit in index.search(term, course=course, documents_only=True)[0]:
                key = hit['entity_id']
                if key not in docs: docs[key]=current_document(db,key)
                if not docs[key]: continue
                found = hits.setdefault(key, {'score':0, 'pages':set()})
                found['score'] += 1
                found['pages'].update(p['page'] for p in hit['pages'])
    # Supplementary material is a relevance sample, never a claim to know the
    # complete exam syllabus. No source crawl or arbitrary filesystem reads.
    supplemental_budget = 16000
    for document_id, hit in sorted(hits.items(), key=lambda pair:(-pair[1]['score'], pair[0]))[:8]:
        doc = docs.get(document_id)
        if not doc:
            continue
        docs[document_id] = doc
        for page in [p for p in doc['pages'] if p.get('page') in hit['pages']][:4]:
            full_text=page.get('text') or ''
            positions=[m.start() for term in terms for m in re.finditer(r'(?<!\w)'+re.escape(term),full_text,re.I)]
            start=max(0,min(positions)-300) if positions else 0
            text=full_text[start:start+min(2500,supplemental_budget)]
            if not text: continue
            if add(document_id,page.get('page'),text,page.get('gaps',[]),len(full_text)>len(text)):
                supplemental_budget-=len(text)
    return context, allowed


def changed(db, steps, con=None):
    checked = {}
    for step in steps:
        for ref in step.get('material_snapshot', step.get('materials', [])):
            document_id = ref['document_id']
            if document_id not in checked:
                checked[document_id] = current_document(db, document_id, con)
            doc = checked[document_id]
            if not doc or doc['hash'] != ref['hash'] or doc['revision'] != ref['revision']:
                return True
            if ref.get('original_only'): continue
            page = next((p for p in doc['pages'] if p.get('page')==ref['page']), None)
            if page is None or hashlib.sha256(encode(page).encode()).hexdigest()!=ref['page_hash']:
                return True
    return False


def document_model_allowed(db, document_id, con=None):
    from .questions import sensitive_mail_document
    doc=db.one("""SELECT d.mime,d.title,d.url,d.original_path,d.analysis_state,s.kind,s.config,s.active,c.active course_active
        FROM documents d JOIN sources s ON s.id=d.source_id LEFT JOIN courses c ON c.id=s.course_id
        WHERE d.id=?""",(document_id,),connection=con)
    if not doc or not doc['active'] or doc['course_active']==0 or doc['analysis_state']=='archived':
        return False
    config=json.loads(doc['config'])
    if (doc['kind']=='mail' or doc['mime']=='message/rfc822') and (
            not config.get('allow_model_analysis',False) or sensitive_mail_document(doc)
            or config.get('model_document_ids') is not None and document_id not in config['model_document_ids']):
        return False
    if doc['url'] in config.get('excluded_urls',[]): return False
    if doc['kind']=='teachcenter' and config.get('forum_since') and urlsplit(doc['url']).path.endswith('/mod/forum/discuss.php'):
        from .collectors import archived_discussion
        try:
            original=Path(doc['original_path'])
            if original.stat().st_size>5*1024*1024: return False
            if archived_discussion(original.read_bytes(),doc['url'],config['forum_since']): return False
        except OSError:
            return False
    return True


def item_model_allowed(db, item_id, con=None):
    return all(document_model_allowed(db,row['document_id'],con) for row in db.rows(
        'SELECT document_id FROM item_evidence WHERE item_id=? AND current=1',(item_id,),connection=con))
