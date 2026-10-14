import importlib.util,pathlib,sys,tempfile,unittest,uuid
from datetime import datetime,timezone
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'src'))
from studycenter_public.storage.db import Database
class ReminderContract(unittest.TestCase):
 def test_due_once_quiet_hours_and_no_mass_catchup(self):
  self.assertIsNotNone(importlib.util.find_spec('studycenter_public.services.reminders'),'Reminder lifecycle missing')
  from studycenter_public.services.reminders import ReminderService
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');svc=ReminderService(db)
   self.assertEqual(svc.settings()['timezone'],'Europe/Vienna')
   svc.save_settings({'enabled':True,'offsets':{'task':[0]}})
   db.add_item({'kind':'task','title':'Current','due_at':'2026-10-14T10:00:00+02:00'})
   db.add_item({'kind':'task','title':'Old','due_at':'2026-09-14T10:00:00+02:00'})
   now=datetime(2026,10,14,8,1,tzinfo=timezone.utc)
   self.assertEqual([j['title'] for j in svc.due(now)],['Current']);self.assertEqual(svc.due(now),[])
   db.add_item({'kind':'task','title':'Night','due_at':'2026-10-14T23:00:00+02:00'})
   self.assertEqual(svc.due(datetime(2026,10,14,21,1,tzinfo=timezone.utc)),[])
 def test_offered_exam_planning_does_not_schedule_personal_exam_reminder(self):
  from studycenter_public.services.reminders import ReminderService
  from studycenter_public.services.actions import ActionService
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');svc=ReminderService(db);svc.save_settings({'enabled':True,'offsets':{'task':[0]}})
   src=db.upsert_source(name='Synthetic');doc=db.save_document({'source_id':src['id']},pages=[{'page':1,'text':'Exam offered at 10:00'}])
   item=db.ingest(doc,{'kind':'exam','title':'Offered exam','date':'2026-10-14','time':'10:00','quote':'Exam offered at 10:00','page':1,'anchor':'x'})
   now=datetime(2026,10,14,8,1,tzinfo=timezone.utc);self.assertEqual(svc.due(now),[])
   ActionService(db).execute([{'type':'set_status','item_id':item['id'],'status':'planned'}],str(uuid.uuid4()))
   self.assertFalse(db.item(item['id'])['booked_event']);self.assertEqual(svc.run_due(now),[])
 def test_registration_opening_default_offsets_and_bell_read_are_independent(self):
  from studycenter_public.services.reminders import ReminderService
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');svc=ReminderService(db);svc.save_settings({'enabled':True})
   item=db.add_item({'kind':'registration','title':'Opening','date':'2026-10-14','time':'10:00','end_date':'2026-10-30','end_time':'18:00','date_meaning':'window'})
   jobs=svc.run_due(datetime(2026,10,14,7,50,tzinfo=timezone.utc));self.assertEqual(len(jobs),1)
   self.assertEqual(svc.notifications()[0]['read'],False);svc.mark_read(jobs[0]['job_key']);self.assertTrue(svc.notifications()[0]['read'])
   self.assertEqual(db.item(item['id'])['status'],'open');self.assertFalse(db.item(item['id'])['booked_event'])
   db.add_item({'kind':'registration','title':'Closing only','date':'2026-10-14','time':'10:00','date_meaning':'deadline'})
   self.assertEqual(svc.run_due(datetime(2026,10,14,8,0,tzinfo=timezone.utc))[0]['title'],'Opening')
   db.add_item({'kind':'task','title':'No time','date':'2026-10-14','date_meaning':'deadline'})
   self.assertEqual(svc.run_due(datetime(2026,10,14,9,0,tzinfo=timezone.utc)),[])
if __name__=='__main__':unittest.main()
