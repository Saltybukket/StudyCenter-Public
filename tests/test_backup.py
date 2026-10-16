import importlib.util,pathlib,sys,tempfile,unittest,zipfile,json
from uuid import uuid4
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'src'))
from studycenter_public.storage.db import Database
class BackupContract(unittest.TestCase):
 def service(self,db,paths):
  self.assertIsNotNone(importlib.util.find_spec('studycenter_public.services.backup'),'Safe backup/restore missing')
  from studycenter_public.services.backup import BackupService
  return BackupService(db,paths)
 def test_backup_restores_originals_decisions_and_relocates_without_profiles(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp).resolve();data=root/'first';data.mkdir();docs=data/'documents';docs.mkdir();paths=SimpleNamespace(data=data,documents=docs,database=data/'db.sqlite3')
   db=Database(paths.database);original=docs/'original.txt';original.write_text('Synthetic original')
   d=db.save_document({'title':'Original','original_path':str(original)},pages=[{'page':1,'text':'Synthetic original'}])
   from studycenter_public.services.actions import ActionService
   actions=[{'type':'create_note','title':'Keep note'}];request_id=str(uuid4())
   receipt_before=ActionService(db).execute(actions,request_id);items_before=db.items()
   (data/'profiles').mkdir();(data/'profiles'/'cookie.txt').write_text('excluded fixture')
   svc=self.service(db,paths);backup=svc.create(root/'backup.zip')
   with zipfile.ZipFile(backup) as z:self.assertFalse(any('profiles' in n or 'cookie' in n for n in z.namelist()))
   other=root/'relocated';other.mkdir();otherdocs=other/'documents';otherdocs.mkdir();otherpaths=SimpleNamespace(data=other,documents=otherdocs,database=other/'db.sqlite3');otherdb=Database(otherpaths.database);otherdb.add_item({'kind':'note','title':'Replaced only explicitly'})
   restore=self.service(otherdb,otherpaths);preview=restore.preview(backup);self.assertEqual(preview['counts']['items'],1)
   self.assertEqual(restore.restore(backup)['preview'],True);self.assertEqual(otherdb.items()[0]['title'],'Replaced only explicitly')
   receipt=restore.restore(backup,confirmed=True);self.assertEqual(otherdb.items()[0]['title'],'Keep note')
   neworiginal=pathlib.Path(otherdb.document(d['id'])['original_path']);self.assertTrue(neworiginal.is_relative_to(otherdocs));self.assertEqual(neworiginal.read_text(),'Synthetic original');self.assertTrue(pathlib.Path(receipt['rollback']).is_file())
   self.assertEqual(neworiginal.name,d['hash']+'.bin')
   self.assertEqual(original.read_text(),'Synthetic original')
   self.assertEqual(otherdb.items(),items_before)
   self.assertEqual(ActionService(otherdb).execute(actions,request_id),receipt_before)
   expected=d | {'original_path':str(neworiginal),'metadata':d['metadata'] | {'original_path':str(neworiginal)}}
   self.assertEqual(otherdb.document(d['id']),expected)
 def test_tampered_archive_and_credentials_fail_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);docs=root/'documents';docs.mkdir();db=Database(root/'db.sqlite3');svc=self.service(db,SimpleNamespace(data=root,documents=docs,database=db.path))
   backup=svc.create(root/'good.zip');bad=root/'bad.zip'
   with zipfile.ZipFile(backup) as source,zipfile.ZipFile(bad,'w') as target:
    for name in source.namelist():target.writestr(name,b'broken' if name=='database.sqlite3' else source.read(name))
   with self.assertRaises(ValueError):svc.preview(bad)
   db.upsert_source(name='Unsafe',metadata={'password':'synthetic'})
   with self.assertRaises(ValueError):svc.create(root/'unsafe.zip')
 def test_mutated_original_is_not_silently_backed_up(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);docs=root/'documents';docs.mkdir();db=Database(root/'db.sqlite3');svc=self.service(db,SimpleNamespace(data=root,documents=docs,database=db.path))
   original=docs/'original.txt';original.write_text('Initial original');db.save_document({'original_path':str(original)},pages=[{'page':1,'text':'Initial original'}]);original.write_text('Changed bytes')
   with self.assertRaises(ValueError):svc.create(root/'changed.zip')
if __name__=='__main__':unittest.main()
