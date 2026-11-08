from datetime import datetime,timedelta
from fastapi.testclient import TestClient
from studycenter_public.core.app import create_app
from .reference_settings import Settings,VIENNA
from studycenter_public.core.models import Candidate


def test_readable_cards_use_linked_closing_and_separate_future_quiz(tmp_path):
    app=create_app(Settings(data_dir=tmp_path));db=app.state.db
    course=db.execute("INSERT INTO courses(title) VALUES('Wahrscheinlichkeitstheorie')")
    today=datetime.now(VIENNA).date();end=today+timedelta(days=1)
    def add(title,identity,**fields):
        value=Candidate(title=title,quote='Gespeicherter Beleg',page=1,anchor='test',**fields)
        return db.execute("INSERT INTO items(identity,course_id,payload,created_at,changed_at) VALUES(?,?,?,'2026-01-01','2026-01-01')",(identity,course,value.model_dump_json()))
    quiz=add('Quiz 2','quiz:2',kind='exam',date=today-timedelta(days=1),time='18:00',end_date=end,end_time='12:00',date_meaning='window')
    note=add('Wahrscheinlichkeitstheorie Quiz 2 bearbeiten','note:quiz',kind='task',date=today,date_meaning='start',description=f'Persönlicher Bearbeitungstag innerhalb des gespeicherten Quizfensters. Originaleintrag http://127.0.0.1:8767/items/{quiz}; Keine Behauptung einer bisherigen Teilnahme.')
    future=add('Quiz – Einheit 6','quiz:future',kind='exam',date=today+timedelta(days=7),time='18:00',end_date=today+timedelta(days=12),end_time='12:00',date_meaning='window')
    before=db.one('SELECT payload FROM items WHERE id=?',(note,))
    client=TestClient(app);page=client.get('/board').text
    assert 'Quiz 2 bearbeiten' in page
    # Compact wall keeps the action title; the full instruction lives in details.
    assert 'Originaleintrag' not in page and 'Keine Behauptung' not in page
    assert 'Quiz schließt: '+end.strftime('%d.%m.%Y') in page
    assert 'Noch nicht verfügbar' in page and 'Quiz zur Einheit 6 bearbeiten' in page
    assert f'data-item-id="{future}"' not in page
    assert f'data-item-id="{note}"' in page
    detail=client.get(f'/items/{note}').text
    assert 'Originaltext & Zeitplanung' in detail and 'Originaleintrag' in detail
    assert 'Beantworte die Fragen im Quiz.' in detail
    events=client.get('/api/calendar').json();shown=next(e for e in events if e['id']==str(note))
    assert shown['start']==end.isoformat()+'T12:00:00'
    assert shown['title']=='Quiz 2 bearbeiten'
    original=next(e for e in events if e['id']==str(quiz))
    assert original['extendedProps']['sourceDate']==(today-timedelta(days=1)).isoformat()
    assert original['extendedProps']['sourceEndDate']==end.isoformat()
    assert db.one('SELECT payload FROM items WHERE id=?',(note,))==before
