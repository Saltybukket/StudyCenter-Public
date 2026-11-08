from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.db import now
from studycenter_public.core.models import Candidate
from studycenter_public.core.wall import calendar_lecture


def test_lecture_classification_preserves_exercises_and_personal_pins():
    def item(title,course='',**extra):
        return dict(identity='import:1',course_title=course,data={'kind':'event','title':title},**extra)
    assert calendar_lecture(item('Vorlesung','Messtechnik'))
    assert calendar_lecture(item('Lehrveranstaltung','Messtechnik (VO)'))
    assert not calendar_lecture(item('Übung zur Vorlesung','Messtechnik'))
    assert not calendar_lecture(item('Lehrveranstaltung','CON (VU)'))
    assert not calendar_lecture(item('Lehrveranstaltung','CON'))  # ambiguous stays
    assert not calendar_lecture(item('Vorlesung',pinned=True))
    own=item('Vorlesung');own['identity']='note:mine'
    assert not calendar_lecture(own)
    own['identity']='import:1';own['data']['kind']='task'
    assert not calendar_lecture(own)


def test_wall_lecture_filter_does_not_change_data_or_calendar(tmp_path):
    app=create_app(Settings(data_dir=tmp_path));db=app.state.db
    ids=[]
    for title in ('Vorlesung','Übung 3 bearbeiten','Eigene Lernaufgabe'):
        value=Candidate(title=title,kind='event' if title=='Vorlesung' else 'task',date='2035-05-02',date_meaning='start',quote='Test',anchor='test',page=1)
        ids.append(db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',(f'fixture:{title}',value.model_dump_json(),now(),now())))
    before=db.rows('SELECT * FROM items')
    client=TestClient(app)
    dom=BeautifulSoup(client.get('/board').text,'html.parser')
    assert not dom.select_one(f'[data-item-id="{ids[0]}"]')
    assert all(dom.select_one(f'[data-item-id="{i}"]') for i in ids[1:])
    assert 'Vorlesungen einblenden' in dom.select_one('.wall-calendar-link').get_text()
    dom=BeautifulSoup(client.get('/board?lectures=true').text,'html.parser')
    assert dom.select_one(f'[data-item-id="{ids[0]}"]')
    assert dom.select_one('.wall-filter-form input[name=lectures]')['value']=='true'
    assert db.rows('SELECT * FROM items')==before
    assert client.get('/calendar').status_code==200
