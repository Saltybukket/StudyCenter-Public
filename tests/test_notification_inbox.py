"""Synthetic paginated read state does not mutate tasks or reminders."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4

from bs4 import BeautifulSoup
from test_web import client
from studycenter_public.storage.db import encode


def test_inbox_paginates_90_days_and_reading_is_independent(tmp_path):
    c=client(tmp_path)
    app=c.app
    app.state.actions.execute([{'type':'create_task','title':'Synthetic task'}],str(uuid4()))
    item=app.state.db.items()[0]
    now=datetime.now(timezone.utc)
    with app.state.db.connection() as con:
        for number in range(23):
            con.execute('INSERT INTO reminder_jobs VALUES(?,?,?,?,?)',(f'job-{number}',item['id'],
                (now-timedelta(minutes=number)).isoformat(),'emitted',encode({'title':f'Synthetic {number}','offset_minutes':0})))
        con.execute('INSERT INTO reminder_jobs VALUES(?,?,?,?,?)',('old',item['id'],
            (now-timedelta(days=91)).isoformat(),'emitted',encode({'title':'Old message','offset_minutes':0})))
    before=app.state.db.item(item['id'])
    first=BeautifulSoup(c.get('/notifications').text,'html.parser')
    assert len(first.select('.notification-group-read'))==20
    assert '23 ungelesen' in first.select_one('#bell-link')['aria-label']
    assert first.find('a',string='Weitere Meldungen')
    assert 'Old message' not in first.get_text()
    groups=[{'item_id':int(node['data-item-id']),'target_second':int(node['data-target-second'])} for node in first.select('.notification-group-read')]
    token=c.get('/api/session').json()['csrf']
    headers={'Origin':app.state.endpoint,'X-CSRF-Token':token}
    result=c.post('/api/notifications/read-groups',json={'groups':groups},headers=headers)
    assert result.status_code==200 and result.json()=={'read':20}
    assert app.state.db.item(item['id'])==before
    assert len(BeautifulSoup(c.get('/notifications').text,'html.parser').select('.notification-group-read'))==3
    history=BeautifulSoup(c.get('/notifications?filter=all').text,'html.parser')
    assert len(history.select('.notification-item'))==20 and 'Gelesen' in history.get_text()
    assert app.state.reminders.unread_count()==3


def test_read_many_is_atomic_and_requires_local_csrf(tmp_path):
    c=client(tmp_path)
    app=c.app
    app.state.actions.execute([{'type':'create_task','title':'Synthetic task'}],str(uuid4()))
    item=app.state.db.items()[0]
    app.state.db.execute('INSERT INTO reminder_jobs VALUES(?,?,?,?,?)',('valid',item['id'],
        datetime.now(timezone.utc).isoformat(),'emitted',encode({'title':'Synthetic','offset_minutes':0})))
    assert c.post('/api/notifications/read-many',json={'job_keys':['valid']}).status_code==403
    token=c.get('/api/session').json()['csrf']
    headers={'Origin':app.state.endpoint,'X-CSRF-Token':token}
    assert c.post('/api/notifications/read-many',json={'job_keys':['valid','missing']},headers=headers).status_code==400
    assert not app.state.reminders.notifications()[0]['read']
    assert app.state.db.item(item['id'])['status']=='open'


def test_same_event_is_one_group_and_reading_it_does_not_read_other_event(tmp_path):
    c=client(tmp_path);app=c.app
    app.state.actions.execute([{'type':'create_task','title':'Synthetic task'}],str(uuid4()))
    item=app.state.db.items()[0];target=datetime.now(timezone.utc).replace(microsecond=0)
    with app.state.db.connection() as con:
        for key,offset in [('first',60),('second',30)]:
            con.execute('INSERT INTO reminder_jobs VALUES(?,?,?,?,?)',(key,item['id'],
                (target-timedelta(minutes=offset)).isoformat(),'emitted',encode({'title':'Synthetic task','offset_minutes':offset})))
        con.execute('INSERT INTO reminder_jobs VALUES(?,?,?,?,?)',('other',item['id'],
            target.isoformat(),'emitted',encode({'title':'Synthetic task','offset_minutes':15})))
    inbox=app.state.reminders.notification_page(unread=True)
    assert inbox['unread']==2 and len(inbox['items'])==2
    group=next(row for row in inbox['items'] if row['messages']==2)
    token=c.get('/api/session').json()['csrf']
    response=c.post('/api/notifications/read-groups',json={'groups':[
        {'item_id':group['item_id'],'target_second':group['target_second']},
        {'item_id':group['item_id'],'target_second':group['target_second']+999999}]},
        headers={'Origin':app.state.endpoint,'X-CSRF-Token':token})
    assert response.status_code==400
    assert not any(n['read'] for n in app.state.reminders.notifications())
    assert app.state.reminders.mark_groups_read([{'item_id':group['item_id'],'target_second':group['target_second']}])=={'read':1}
    by_id={n['job_key']:n for n in app.state.reminders.notifications()}
    assert by_id['first']['read'] and by_id['second']['read'] and not by_id['other']['read']
    assert app.state.db.item(item['id'])['status']=='open'
