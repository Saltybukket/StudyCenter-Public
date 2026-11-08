"""Idle workers avoid full projections but still detect external writes and time."""
import sqlite3
from contextlib import closing
from datetime import datetime
from types import SimpleNamespace

import pytest

from studycenter_public.core.app import item_timing
from studycenter_public.core.config import VIENNA
from studycenter_public.core.db import Database,now
from studycenter_public.core.models import Candidate
from studycenter_public.core.reminders import ReminderService
from studycenter_public.core.class_completion import ClassCompletionService


class Passes:
    def __init__(self,total,after=None):self.count=0;self.total=total;self.after=after
    def is_set(self):return self.count>=self.total
    async def wait(self):
        self.count+=1
        if self.after:self.after(self.count)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['reminder','completion'])
@pytest.mark.parametrize('change',['external','midnight','during-work','failure'])
async def test_idle_watch_rebuilds_only_when_needed_and_closes(tmp_path,monkeypatch,kind,change):
    import studycenter_public.core.reminders as reminders
    import studycenter_public.core.class_completion as completion
    module=reminders if kind=='reminder' else completion
    db=Database(tmp_path/'idle.sqlite')
    service=ReminderService(db,item_timing) if kind=='reminder' else ClassCompletionService(db)
    def write():
        # A different connection/Database is intentionally not an in-memory signal.
        with sqlite3.connect(db.path) as external:
            external.execute("INSERT OR REPLACE INTO app_preferences VALUES('idle-test','changed')")
    stop=Passes(4,lambda n:write() if change=='external' and n==2 else None)
    service.stop=stop
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2030,4,4 if change=='midnight' and stop.count>=2 else 3,12,tzinfo=VIENNA)
    monkeypatch.setattr(module,'datetime',Clock)
    calls=[];watches=[]
    def tick(*args):
        rebuild=args[1] if kind=='reminder' else True
        calls.append((stop.count,rebuild))
        if change=='during-work' and stop.count==0:write()
        if change=='failure' and stop.count==0:raise RuntimeError('temporary failure')
    monkeypatch.setattr(service,'tick',tick)
    def captured(connection):watches.append(connection);return closing(connection)
    monkeypatch.setattr(module,'closing',captured)
    await service.run()
    rebuilds=[iteration for iteration,rebuild in calls if rebuild]
    assert rebuilds==([0,1] if change in ('during-work','failure') else [0,2])
    if kind=='reminder':assert len(calls)==4  # delivery still runs every pass
    assert not service.running and not service.busy
    assert len(watches)==1
    with pytest.raises(sqlite3.ProgrammingError):watches[0].execute('SELECT 1')


@pytest.mark.parametrize('done',[False,True])
def test_delivery_without_rebuild_still_checks_current_status(tmp_path,done):
    db=Database(tmp_path/'due.sqlite');sent=[]
    value=Candidate(title='Abgabe',kind='task',date='2030-04-03',time='12:00',date_meaning='deadline',obligation='required',quote='Eigene Aufgabe',page=1,anchor='own')
    item=db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',('note:due',value.model_dump_json(),now(),now()))
    service=ReminderService(db,item_timing,SimpleNamespace(send=lambda *args:sent.append(args)))
    service.reconcile(datetime(2030,4,3,8,tzinfo=VIENNA))
    if done:db.execute("UPDATE items SET status='done' WHERE id=?",(item,))
    service.tick(datetime(2030,4,3,10,tzinfo=VIENNA),reconcile=False)
    assert len(sent)==(0 if done else 1)
    if done:assert not db.one("SELECT id FROM reminder_jobs WHERE state='delivered'")
