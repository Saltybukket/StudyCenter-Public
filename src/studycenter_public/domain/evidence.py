"""Source fact resolution with retained field-level provenance."""
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)

def temporal_claim(value):
    """An explicitly quoted open window is not a personally scheduled exam start.

    Interpret the extracted metadata without rewriting the original evidence.
    A duration alone or an ordinary exam start/end must not trigger this repair.
    """
    if (value.get('kind') == 'exam' and value.get('date_meaning') == 'start'
            and value.get('date') and value.get('end_date')
            and re.search(r'\b(?:offen\s+von|open\s+from)\b', value.get('quote', ''), re.I)):
        return value | {'date_meaning': 'window'}
    return value


CLAIM_FIELDS = ('date', 'time', 'end_date', 'end_time', 'obligation', 'source_state', 'date_meaning')
INTERNAL_CONFLICT = re.compile(
    r'\b(?:Widerspruch\s+zwischen|widerspr[üu]chliche?\s+(?:Dat|Termin|Frist|Schlie[ßs]|Anmelde)|widersprechen\s+sich|abweichend(?:e|er|en)?\s+(?:Frist|Termin|Schlie[ßs]|Datums)|conflicting\s+(?:dat|deadline)|contradictory\s+(?:dat|deadline))', re.I)


def claims_conflict(claims):
    facts = [claim_facts(c) for c in claims]
    return any(len({encode(c[f]) for c in facts if f in c}) > 1
               for f in {key for c in facts for key in c})


def claim_facts(value):
    """Compare dates serving the same purpose, including closing-only copies."""
    value = temporal_claim(value)
    facts = {f:value[f] for f in ('obligation','source_state')
             if value.get(f) not in (None,'unclear','none')}
    kind, meaning = value.get('kind'), value.get('date_meaning')
    closing = (kind == 'task' and meaning in ('deadline','window')
               or kind in ('registration','exam') and meaning == 'window')
    if closing:
        if value.get('end_date'):
            roles = [('opening', 'date', 'time'), ('closing', 'end_date', 'end_time')]
        else:
            roles = [('closing', 'date', 'time')]
    else:
        roles = [('start', 'date', 'time'), ('end', 'end_date', 'end_time')]
    for role, day, clock in roles:
        if value.get(day):
            facts[role+'_date'] = value[day]
            if value.get(clock):
                facts[role+'_time'] = (value[day], value[clock])
    return facts


def governing_reference(value):
    text = ' '.join(value.get(f) or '' for f in ('quote','description','uncertainty'))
    authority = r'(?:maßgeblich|massgeblich|verbindlich(?:en|e|er)?|authoritative|governed\s+by)'
    source = r'(?:kalender|calendar|FAQ|Kursseite|course\s+page|https?://)'
    reference = r'(?:Termine|Fristen|dates|deadlines).{0,80}(?:siehe|entnehmen|laut|according\s+to).{0,80}'+source
    return text if re.search(authority+r'.{0,160}'+source+'|'+source+r'.{0,160}'+authority+'|'+reference, text, re.I) else ''


def effective_evidence(proofs, fallback, claims=None, context_lookup=None):
    """Resolve source facts without changing evidence, status or manual corrections.

    Publication establishes cross-source order. Retrieval time does not. Unknown
    publication dates, ties and conflicting statements in one source stay open.
    """
    claims = claims if claims is not None else [temporal_claim(json.loads(p['payload'])) for p in proofs]
    normalized = temporal_claim(fallback)
    facts = [claim_facts(c) for c in claims]
    if (len(claims) == 1 and claims[0] == normalized
            and not INTERNAL_CONFLICT.search(normalized.get('uncertainty') or '')):
        # Most saved entries have one unchanged assertion. Keep the same complete
        # role provenance without rebuilding and sorting a resolution graph.
        payload = dict(normalized)
        if 'closing_date' in facts[0]:
            payload['date_meaning'] = 'window' if payload.get('kind') in ('exam','registration') or 'opening_date' in facts[0] else 'deadline'
        p = proofs[0]
        citation = dict(document_id=p['document_id'],page=p['page'],quote=p['quote'],published_at=p.get('published_at'))
        return payload, False, False, {'superseded_fields':[], 'unresolved_fields':[],
            'internal_conflict':False, 'superseded_internal_conflicts':[],
            'extraction_ambiguities':[], 'facts':{field:[citation] for field in facts[0]}}
    ambiguous_policy = set()
    if context_lookup is not None:
        def sentence(index):
            p = proofs[index]
            text = ' '.join(context_lookup(p['document_id'],p['page']).split()).casefold()
            quote = ' '.join(p['quote'].split()).casefold()
            position = text.find(quote)
            if position < 0 or text.count(quote) != 1:return None
            start = text.rfind('.',0,position)+1
            previous = text.rfind('.',0,max(0,start-1))+1
            # Course pages place heading/navigation text before the first prose sentence
            # without punctuation. Retain the setup-policy sentence itself.
            prose = text.rfind('to ensure ',previous,start)
            start = prose if prose >= 0 else previous
            end = text.find('.',position+len(quote))
            return text[start:end+1 if end >= 0 else None].strip()
        for i,left in enumerate(proofs):
            for j in range(i+1,len(proofs)):
                right = proofs[j]
                if {facts[i].get('obligation'),facts[j].get('obligation')} != {'required','recommended'}:continue
                a,b = urlsplit(left.get('url') or ''),urlsplit(right.get('url') or '')
                if (left.get('kind') != 'teachcenter' or right.get('kind') != 'teachcenter'
                        or left.get('source_id') is None or left.get('source_id') != right.get('source_id')
                        or a.hostname != b.hostname or {a.path,b.path} != {'/main/course/view.php','/main/course/section.php'}
                        or len(left['quote']) < 30 or ' '.join(left['quote'].split()).casefold() != ' '.join(right['quote'].split()).casefold()):continue
                context = sentence(i)
                if context and context == sentence(j):ambiguous_policy.update((i,j))
    winners, unresolved, provenance, superseded = {}, [], {}, []
    for field in sorted({key for f in facts for key in f}, key=lambda f:f.endswith('_time')):
        asserted = [(i,f[field]) for i,f in enumerate(facts) if field in f]
        if field == 'obligation' and ambiguous_policy:
            remaining = [(i,v) for i,v in asserted if i not in ambiguous_policy]
            if len({v for _,v in remaining}) <= 1:
                # Identical native course/section text received two model labels.
                # Preserve the uncertainty instead of inventing a source verdict.
                winners[field] = 'unclear'
                provenance[field] = [dict(document_id=proofs[i]['document_id'],page=proofs[i]['page'],quote=proofs[i]['quote'],
                                          published_at=proofs[i].get('published_at')) for i,_ in asserted]
                continue
            asserted = remaining
        if field.endswith('_time') and field.replace('_time','_date') in winners:
            asserted = [(i,v) for i,v in asserted if v[0] == winners[field.replace('_time','_date')]]
        if not asserted:continue
        values = {encode(v) for _,v in asserted}
        selected = asserted
        if len(values) > 1:
            newest = [entry for entry in asserted if all(
                entry[0] == other[0] or proofs[entry[0]].get('document_id') == proofs[other[0]].get('document_id')
                or newer_publication(proofs[entry[0]].get('published_at'), proofs[other[0]].get('published_at'))
                for other in asserted)]
            if newest:
                references = [governing_reference(claims[i]) for i,_ in newest]
                if not any(references):
                    selected = newest
                else:
                    # Resolve only an exact retained URL/title reference. A vague
                    # "see calendar" cannot manufacture an authority relationship.
                    referenced = [(i,v) for i,v in asserted for p in [proofs[i]]
                                  if i not in {n for n,_ in newest} and any(
                        p.get('url') and p['url'] in ref
                        or len(p.get('title') or '') > 8 and p['title'].casefold() in ref.casefold()
                        for ref in references if ref)]
                    if len({proofs[i]['document_id'] for i,_ in referenced}) == 1:
                        selected = referenced
                if selected != asserted:superseded.append(field)
            if len({encode(v) for _,v in selected}) > 1:
                unresolved.append(field)
        if field not in unresolved:
            winners[field] = selected[-1][1]
            provenance[field] = [dict(document_id=proofs[i]['document_id'],page=proofs[i]['page'],
                                      quote=proofs[i]['quote'],published_at=proofs[i].get('published_at'))
                                 for i,_ in selected]
    # Prefer a compatible complete span so an opening is not lost when another
    # source gives only its closing deadline. Preserve the selected source text.
    compatible = [c for c,f in zip(claims,facts) if all(
        key not in winners or val == winners[key] for key,val in f.items())]
    if all(key not in winners or val == winners[key] for key,val in claim_facts(normalized).items()):
        compatible.insert(0,normalized)
    base = max(compatible, key=lambda c:sum(c.get(f) is not None for f in ('date','time','end_date','end_time')),
               default=normalized)
    payload = dict(base)
    for field in ('obligation','source_state'):
        if field in winners:payload[field] = winners[field]
    closing = 'closing_date' in winners
    if closing:
        opening = winners.get('opening_date')
        payload.update(date=opening or winners['closing_date'], end_date=winners['closing_date'] if opening else None,
                       end_time=None,
                       date_meaning='window' if payload.get('kind') in ('exam','registration') or opening else 'deadline')
        roles = [('opening' if opening else 'closing','date','time'),('closing','end_date','end_time')]
    else:
        roles = [('start','date','time'),('end','end_date','end_time')]
    for role, day, clock in roles:
        if role+'_date' in winners and (not closing or payload.get(day)):
            payload[day] = winners[role+'_date']
            timed = winners.get(role+'_time')
            payload[clock] = timed[1] if timed and timed[0] == payload[day] else None
    conflict_text = INTERNAL_CONFLICT
    internal_texts, clarified_texts, clarified_proofs = [], [], []
    internal_fields = set()
    for index,claim in enumerate(claims):
        text = claim.get('uncertainty') or ''
        if not conflict_text.search(text):continue
        temporal_ambiguity = re.search(r'Datum|Datums|Termin|Frist|Schlie[ßs]|Wochentag|\bKW\b|date|deadline',text,re.I)
        affected = {key for key in facts[index] if key.endswith('_date')} if temporal_ambiguity else set()
        if re.search(r'Schlie[ßs]|Frist|deadline|closing',text,re.I) and 'closing_date' in affected:
            affected = {'closing_date'}
        if len(set(re.findall(r'\d{1,2}:\d{2}',text))) > 1 or re.search(r'Uhrzeit|Schlie[ßs]zeit|closing\s+time',text,re.I):
            affected |= {key.replace('_date','_time') for key in affected}
        # Only a precisely ordered source asserting every affected fact can clear
        # an older source's internal ambiguity. Undated or partial replies cannot.
        clarified = affected and all(key in provenance and any(
            newer_publication(p.get('published_at'),proofs[index].get('published_at'))
            and not any(conflict_text.search(c.get('uncertainty') or '') for c,q in zip(claims,proofs)
                        if q['document_id'] == p['document_id'])
            for p in provenance[key]) for key in affected)
        if not clarified:
            internal_texts.append(text)
            internal_fields.update(affected or {'obligation' if not temporal_ambiguity else 'start_date'})
        else:
            clarified_texts.append(text)
            clarified_proofs.append(dict(document_id=proofs[index]['document_id'],page=proofs[index]['page'],quote=proofs[index]['quote']))
    if payload.get('uncertainty') in clarified_texts and not internal_texts:
        payload['uncertainty'] = None
    ambiguities = []
    if ambiguous_policy:
        reason = 'Pflichtgrad unklar: Derselbe TeachCenter-Text wurde als verpflichtend und empfohlen ausgewertet.'
        if reason not in (payload.get('uncertainty') or ''):
            payload['uncertainty'] = ' '.join(filter(None,[payload.get('uncertainty'),reason]))
        ambiguities.append({'field':'obligation','reason':reason,'document_ids':sorted({proofs[i]['document_id'] for i in ambiguous_policy})})
    internal = bool(internal_texts)
    internal_date = any(re.search(r'Datum|Datums|Termin|Frist|Schlie[ßs]|Wochentag|\bKW\b|date|deadline',t,re.I) for t in internal_texts)
    return payload, bool(unresolved) or internal, any(f.endswith(('_date','_time')) for f in unresolved) or internal_date, {
        'superseded_fields':sorted(superseded), 'unresolved_fields':sorted(unresolved),
        'internal_conflict':internal, 'internal_unresolved_fields':sorted(internal_fields), 'superseded_internal_conflicts':clarified_proofs, 'extraction_ambiguities':ambiguities, 'facts':provenance}


def newer_publication(new, old):
    if not new or not old:
        return False
    try:
        left, right = datetime.fromisoformat(new), datetime.fromisoformat(old)
        if 'T' in new and 'T' in old and left.tzinfo and right.tzinfo:
            return left > right
        # Unknown time on the same day cannot establish ordering.
        return left.date() > right.date()
    except ValueError:
        return False


def newer_observation(new, old):
    """A precise new fetch can establish the current hash of this exact URL."""
    if not new or not old or 'T' not in new or 'T' not in old:return False
    try:
        left,right = datetime.fromisoformat(new),datetime.fromisoformat(old)
        return bool(left.tzinfo and right.tzinfo and left > right)
    except ValueError:
        return False
