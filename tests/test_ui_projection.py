"""Personal UI values remain projections of persisted Public state."""
from uuid import uuid4

from test_web import client


def test_saved_item_reminders_and_window_fields_render(tmp_path):
    c=client(tmp_path)
    c.app.state.actions.execute([{'type':'create_task','title':'Synthetic window',
        'date':'2026-10-20','end_date':'2026-10-22','end_time':'16:00','date_meaning':'window'}],str(uuid4()))
    item=c.app.state.db.items()[0]
    c.app.state.reminders.configure({'exceptions':{str(item['id']):{'enabled':False,'offsets':[90,15]}}})
    page=c.get('/items/'+str(item['id']))
    assert page.status_code==200
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(page.text,'html.parser')
    form=soup.select_one('#personal-reminder')
    assert form.select_one('[name=minutes]')['value']=='90,15'
    assert not form.select_one('[name=enabled]').has_attr('checked')
    edit=soup.select_one('#edit-form')
    assert edit.select_one('[name=end_date]')['value']=='2026-10-22'
    assert edit.select_one('[name=end_time]')['value']=='16:00'


def test_overview_uses_effective_window_end_not_insert_order(tmp_path):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    c=client(tmp_path)
    today=datetime.now(VIENNA).date()
    actions=[{'type':'create_task','title':'Synthetic later','date':str(today+timedelta(days=40))},
             {'type':'create_task','title':'Synthetic window','date':str(today-timedelta(days=1)),
              'end_date':str(today+timedelta(days=2)),'date_meaning':'window'}]
    c.app.state.actions.execute(actions,str(uuid4()))
    response=c.get('/overview')
    assert response.status_code==200
    assert [i['title'] for i in response.context['week_active']]==['Synthetic window']
    assert [i['title'] for i in response.context['focus_actions']]==['Synthetic window']


def test_linux_operation_control_is_explicitly_unavailable(tmp_path,monkeypatch):
    from studycenter_public.web import pages
    c=client(tmp_path)
    monkeypatch.setattr(pages.sys,'platform','linux')
    response=c.get('/settings')
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(response.text,'html.parser')
    assert soup.select_one('#background').has_attr('disabled')
    assert 'Nur unter Windows' in soup.select_one('#background-availability').text


def test_partial_reminder_exception_inherits_service_defaults(tmp_path):
    c=client(tmp_path)
    c.app.state.actions.execute([{'type':'create_task','title':'Synthetic inheritance'}],str(uuid4()))
    item=c.app.state.db.items()[0]
    c.app.state.reminders.configure({'exceptions':{str(item['id']):{'offsets':[60]}}})
    from bs4 import BeautifulSoup
    page=BeautifulSoup(c.get('/items/'+str(item['id'])).text,'html.parser')
    assert page.select_one('#personal-reminder [name=enabled]').has_attr('checked')
    c.app.state.reminders.configure({'exceptions':{str(item['id']):{'enabled':False}}})
    page=BeautifulSoup(c.get('/items/'+str(item['id'])).text,'html.parser')
    assert page.select_one('#personal-reminder [name=minutes]')['value']=='1440,120'
    assert not page.select_one('#personal-reminder [name=enabled]').has_attr('checked')


def test_ui_assets_have_content_versions_for_existing_browser_caches(tmp_path):
    from bs4 import BeautifulSoup
    from studycenter_public.web.pages import static_url
    c=client(tmp_path)
    soup=BeautifulSoup(c.get('/settings').text,'html.parser')
    assert soup.select_one('script[src*="public.js"]')['src']==static_url('public.js')
    assert soup.select_one('link[href*="settings.css"]')['href']==static_url('settings.css')
    assert c.get(static_url('public.js')).status_code==200
