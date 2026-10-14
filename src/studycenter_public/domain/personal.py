"""Booking proof is bound to a matching source slot, never inferred from a TODO."""
import json
import re


def booking_projection(value, evidence):
    result=[]
    for proof in evidence:
        claim=json.loads(proof['payload'])
        quote=proof['quote']
        if claim.get('kind') not in ('event','exam'):
            continue
        if not value.get('date') or not value.get('time'):
            continue
        if any(claim.get(k)!=value.get(k) for k in ('date','time','end_date','end_time')):
            continue
        if re.search(r'cancelled|abgesagt|nicht angemeldet|not booked',quote,re.I):
            continue
        if re.search(r'your booking (?:was|is) successful|Ihre Eintragung war erfolgreich|\bBereits angemeldet\b',quote,re.I):
            result.append({'document_id':proof['document_id'],'page':proof['page'],'quote':quote,'role':'personal_booking','current':True})
    return result
