import importlib.util,pathlib,sys,tempfile,unittest,uuid
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'src'))
from studycenter_public.storage.db import Database
class ActionContract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Database(pathlib.Path(self.tmp.name)/'db.sqlite3')
 def service(self):
  self.assertIsNotNone(importlib.util.find_spec('studycenter_public.services.actions'),'Action transactions missing')
  from studycenter_public.services.actions import ActionService
  return ActionService(self.db)
 def test_uuid_receipt_retries_body_conflicts_and_atomic_rollback(self):
  svc=self.service();rid=str(uuid.uuid4());actions=[{'type':'create_task','title':'Synthetic TODO'}]
  self.assertEqual(svc.preview(actions)['count'],1);self.assertEqual(self.db.items(),[])
  receipt=svc.execute(actions,rid);self.assertEqual(svc.execute(actions,rid),receipt);self.assertEqual(len(self.db.items()),1)
  with self.assertRaises(ValueError):svc.execute([{'type':'create_note','title':'Different'}],rid)
  with self.assertRaises(ValueError):svc.execute([{'type':'create_note','title':'Rollback'},{'type':'set_status','item_id':999,'status':'done'}],str(uuid.uuid4()))
  self.assertEqual(len(self.db.items()),1)
 def test_undo_reverses_only_unchanged_fields(self):
  svc=self.service();item=self.db.add_item({'kind':'task','title':'Initial'});rid=str(uuid.uuid4())
  svc.execute([{'type':'set_status','item_id':item['id'],'status':'done'}],rid,{str(item['id']):1})
  svc.execute([{'type':'set_override','item_id':item['id'],'fields':{'title':'Later title'}}],str(uuid.uuid4()))
  undo_id=str(uuid.uuid4());receipt=svc.undo(rid,undo_id)
  self.assertEqual(svc.undo(rid,undo_id),receipt)
  current=self.db.item(item['id']);self.assertEqual(current['status'],'open');self.assertEqual(current['data']['title'],'Later title')
  with self.assertRaises(ValueError):svc.execute([{'type':'set_status','item_id':item['id'],'status':'planned'}],str(uuid.uuid4()),{str(item['id']):1})
 def test_source_decision_stays_personal_and_becomes_stale(self):
  svc=self.service();source=self.db.upsert_source(name='Synthetic',kind='public');d=self.db.save_document({'source_id':source['id'],'url':'https://example.test','hash':'a','published_at':'2026-01-01'},pages=[{'page':1,'text':'Deadline October 30'}])
  candidate={'kind':'task','title':'Exercise','identity_scope':'course','identity_key':'x','quote':'Deadline October 30','page':1,'anchor':'x','date':'2026-10-30','date_meaning':'deadline'}
  item=self.db.ingest(d,candidate)
  svc.execute([{'type':'decide_source','item_id':item['id'],'field':'date','value':'2026-10-29','document_id':d['id'],'reason':'Personal planning'}],str(uuid.uuid4()))
  d2=self.db.save_document({'source_id':source['id'],'url':'https://example.test','hash':'b','published_at':'2026-02-01'},pages=[{'page':1,'text':'Deadline October 31'}]);self.db.ingest(d2,candidate|{'date':'2026-10-31','quote':'Deadline October 31'})
  item=self.db.item(item['id']);self.assertEqual(item['data']['date'],'2026-10-29');self.assertEqual(item['source_payload']['date'],'2026-10-31');self.assertTrue(item['personal_decisions'][0]['stale'])
 def test_undo_conflict_blocks_entire_batch_and_rejects_invalid_fields(self):
  svc=self.service();a=self.db.add_item({'kind':'task','title':'First'});b=self.db.add_item({'kind':'task','title':'Second'});rid=str(uuid.uuid4())
  svc.execute([{'type':'set_status','item_id':a['id'],'status':'done'},{'type':'set_status','item_id':b['id'],'status':'done'}],rid)
  svc.execute([{'type':'set_status','item_id':b['id'],'status':'planned'}],str(uuid.uuid4()))
  receipt=svc.undo(rid,str(uuid.uuid4()));self.assertEqual(receipt['state'],'blocked');self.assertEqual(self.db.item(a['id'])['status'],'done');self.assertEqual(self.db.item(b['id'])['status'],'planned')
  for action in [{'type':'create_task','title':'Bad','date':'tomorrow'},{'type':'create_note','title':'Bad','credential':'unexpected'},{'type':'set_override','item_id':a['id'],'fields':{'source_state':'booked'}}]:
   with self.assertRaises(ValueError):svc.execute([action],str(uuid.uuid4()))
if __name__=='__main__':unittest.main()
