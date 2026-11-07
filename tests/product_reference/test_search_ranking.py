from datetime import datetime, timedelta

from .reference_settings import Settings, VIENNA
from studycenter_public.core.db import Database, encode, now
from studycenter_public.core.documents import DocumentStore
from studycenter_public.core.models import Candidate
from studycenter_public.core.search import SearchIndex


def setup(tmp_path):
    db=Database(tmp_path/'campusboard.sqlite3')
    course=db.execute("INSERT INTO courses(title,semester) VALUES('Messtechnik','2026W')")
    source=db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Material','https://example.org')",(course,))
    store=DocumentStore(db,Settings(data_dir=tmp_path))
    def item(title,kind='task',status='open',days=2,ignored=0):
        day=datetime.now(VIENNA).date()+timedelta(days=days) if days is not None else None
        value=Candidate(title=title,kind=kind,date=day,date_meaning='deadline' if kind=='task' else 'start',quote='Synthetic fixture',page=1,anchor='fixture')
        return db.execute('INSERT INTO items(identity,course_id,payload,status,ignored,created_at,changed_at) VALUES(?,?,?,?,?,?,?)',
            ('note:'+title,course,value.model_dump_json(),status,ignored,now(),now()))
    def document(title,text):
        identifier=store.save(source,'https://example.org/'+str(len(title)),title,'application/pdf',text.encode())
        db.execute('UPDATE documents SET pages=? WHERE id=?',(encode([{'page':1,'anchor':'one','text':text,'gaps':[]}]),identifier))
        return identifier
    return db,SearchIndex(db),item,document


def test_current_tasks_and_relevant_material_precede_completed_and_elapsed(tmp_path):
    db,index,item,document=setup(tmp_path)
    done=item('Messtechnik alte Abgabe',status='done',days=-20)
    past=item('Messtechnik Vorlesung',kind='event',days=-10)
    current=item('Messtechnik AB2 abgeben')
    overdue=item('Messtechnik AB1 nachreichen',days=-3)
    pdf=document('Messtechnik Arbeitsblatt','Messtechnik Aufgaben und Schaltungen')
    before=db.rows('SELECT * FROM items')
    found,total,_=index.search(prioritize_current=True, query='Messtechnik')
    positions={(r['kind'],r['entity_id']):i for i,r in enumerate(found)}
    for key in [('item',current),('item',overdue),('document',pdf)]:
        assert positions[key]<positions['item',done]
        assert positions[key]<positions['item',past]
    assert total==5 and before==db.rows('SELECT * FROM items')
    db.execute("UPDATE items SET status='done' WHERE id=?",(current,))
    next_results,_,_=index.search(prioritize_current=True, query='Messtechnik')
    assert next_results[0]['entity_id']==overdue and next_results[0]['kind']=='item'


def test_material_topic_match_beats_course_only_task_and_filters_still_work(tmp_path):
    _,index,item,document=setup(tmp_path)
    task=item('Tasche packen',days=None)
    pdf=document('Messtechnik AB2','Messbrücke und Widerstand berechnen')
    old=item('Messtechnik Archiv',status='done',days=-90,ignored=1)
    found,total,_=index.search(prioritize_current=True, query='Messtechnik')
    assert found[0]['kind']=='document' and found[0]['entity_id']==pdf
    assert total==2
    historical,_,_=index.search(prioritize_current=True, query='Messtechnik',history=True)
    assert historical[-1]['entity_id']==old
    assert index.search(prioritize_current=True, query='Messtechnik',content='pdf')[1]==1
    assert index.search(prioritize_current=True, query='Tasche')[0][0]['entity_id']==task
    assert index.search(prioritize_current=True, query='Archiv',history=True)[0][0]['entity_id']==old


def test_ranking_before_pagination_and_no_fetched_date_boost(tmp_path):
    _,index,item,document=setup(tmp_path)
    for number in range(24):item(f'Messtechnik erledigt {number}',status='done',days=-number)
    pdf=document('Messtechnik passende Unterlage','Messtechnik')
    current=item('Messtechnik aktuelle Abgabe')
    first,total,page=index.search(prioritize_current=True, query='Messtechnik')
    second,_,_=index.search(prioritize_current=True, query='Messtechnik',page=2)
    keys=lambda rows:{(r['kind'],r['entity_id']) for r in rows}
    assert total==26 and page==1
    assert {('item',current),('document',pdf)}<=keys(first)
    assert keys(first).isdisjoint(keys(second)) and len(first)+len(second)==26


def test_body_relevance_is_not_crowded_out_by_partial_title_and_metadata(tmp_path):
    import sqlite3
    from contextlib import closing
    db=Database(tmp_path/'campusboard.sqlite3');index=SearchIndex(db);index.refresh()
    with closing(sqlite3.connect(index.path)) as con,con:
        for number in range(21):
            title='Original' if number==0 else 'Labormessung'
            body=('Labormessung Fehlergrenze '*15) if number==0 else 'Nebeninformation'
            details='' if number==0 else 'Fehlergrenze'
            meta=encode({'hash':str(number),'label':'PDF'})
            con.execute('INSERT INTO search VALUES(?,?,?,?,?,?,?,?,?,?)',('document',number,1,'pdf',1,1,title,body,details,meta))
    for current in (False,True):
        found,total,_=index.search('Labormessung Fehlergrenze',documents_only=True,prioritize_current=current)
        assert total==21 and found[0]['entity_id']==0
