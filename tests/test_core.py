import importlib.util, pathlib, sys, tempfile, unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'src'))
class CoreContract(unittest.TestCase):
 def test_empty_database_search_evidence_and_projection(self):
  self.assertIsNotNone(importlib.util.find_spec('studycenter_public.storage.db'), 'Database domain missing')
  from studycenter_public.storage.db import Database
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3')
   self.assertEqual(db.items(),[])
   source=db.upsert_source(name='Synthetic course',kind='public')
   d=db.save_document({'source_id':source['id'],'url':'https://example.test/course','title':'Synthetic deadline','hash':'a','published_at':'2026-10-01'},pages=[{'page':1,'text':'Submit exercise by 2026-10-30.'}])
   item=db.ingest(d,{'title':'Exercise','kind':'task','date':'2026-10-30','date_meaning':'deadline','quote':'Submit exercise by 2026-10-30.','page':1,'anchor':'deadline','identity_key':'exercise-1','identity_scope':'course'})
   self.assertEqual(item['data']['date'],'2026-10-30')
   self.assertEqual(db.items(query='exercise')[0]['id'],item['id'])
   self.assertEqual(item['evidence'][0]['quote'],'Submit exercise by 2026-10-30.')
   self.assertFalse(item['booked_event'])
   with db.connection() as con:self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],1)
 def test_quote_must_be_bound_to_retained_original(self):
  self.assertIsNotNone(importlib.util.find_spec('studycenter_public.storage.db'),'Database domain missing')
  from studycenter_public.storage.db import Database
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3')
   d=db.save_document({'title':'Original'},pages=[{'page':1,'text':'Actual original'}])
   with self.assertRaises(ValueError):db.ingest(d,{'title':'Invented','kind':'task','quote':'Fabricated quote','page':1,'anchor':'x'})
   self.assertEqual(db.items(),[])
 def test_atomic_ingest_rejects_bad_second_candidate_and_typed_dates(self):
  from studycenter_public.storage.db import Database
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');doc=db.save_document({'title':'Original'},pages=[{'page':1,'text':'Actual first quote and actual second quote'}])
   first={'title':'First','kind':'task','quote':'Actual first quote','page':1,'anchor':'first'}
   second={'title':'Second','kind':'task','quote':'invented quote','page':1,'anchor':'second'}
   with self.assertRaises(ValueError):db.ingest_many(doc,[first,second])
   self.assertEqual(db.items(),[])
   with self.assertRaises(ValueError):db.ingest(doc,first|{'date':'2026-99-99'})
   self.assertEqual(db.items(),[])
 def test_delayed_older_source_preserves_new_current_claim(self):
  from studycenter_public.storage.db import Database
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');src=db.upsert_source(name='Synthetic')
   current=db.save_document({'source_id':src['id'],'url':'https://example.test/task','hash':'new','published_at':'2026-10-10','fetched_at':'2026-10-10T08:00:00Z'},pages=[{'page':1,'text':'October 30 deadline'}])
   candidate={'kind':'task','title':'Task','quote':'October 30 deadline','page':1,'anchor':'deadline','identity_key':'task','identity_scope':'course','date':'2026-10-30','date_meaning':'deadline'}
   item=db.ingest(current,candidate)
   older=db.save_document({'source_id':src['id'],'url':'https://example.test/task','hash':'old','published_at':'2026-10-01','fetched_at':'2026-10-12T08:00:00Z'},pages=[{'page':1,'text':'October 20 deadline'}]);db.ingest(older,candidate|{'date':'2026-10-20','quote':'October 20 deadline'})
   updated=db.item(item['id']);self.assertEqual(updated['source_payload']['date'],'2026-10-30');self.assertEqual(len(updated['evidence']),2)
 def test_explicit_rereading_keeps_original_pages_and_adds_revision(self):
  from studycenter_public.storage.db import Database
  with tempfile.TemporaryDirectory() as tmp:
   db=Database(pathlib.Path(tmp)/'db.sqlite3');doc=db.save_document({'url':'https://example.test/read','hash':'same'},pages=[{'page':1,'text':'First reading'}])
   reread=db.save_document({'url':'https://example.test/read','hash':'same','force_revision':True},pages=[{'page':1,'text':'Corrected reading'}])
   self.assertNotEqual(doc['id'],reread['id']);self.assertEqual(reread['revision'],2);self.assertEqual(db.pages(doc['id'])[0]['text'],'First reading')
if __name__=='__main__':unittest.main()
