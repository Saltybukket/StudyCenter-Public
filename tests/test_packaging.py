import importlib.util,pathlib,sys,tempfile,unittest,json,tarfile
import os,subprocess
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
class PackagingContract(unittest.TestCase):
 def test_tar_archive_preserves_executable_and_manifest_excludes_runtime_data(self):
  self.assertIsNotNone(importlib.util.find_spec('scripts.build_release'),'Reproducible packaging missing')
  from scripts.build_release import archive_linux,write_manifest
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);bundle=root/'StudyCenter-Public';bundle.mkdir();exe=bundle/'StudyCenter-Public';exe.write_bytes(b'#!/bin/sh\nexit 0\n');exe.chmod(0o755)
   write_manifest(bundle,'test-version');archive=archive_linux(bundle,root,'test-version')
   with tarfile.open(archive) as tar:
    member=tar.getmember('StudyCenter-Public/StudyCenter-Public');self.assertTrue(member.mode & 0o111)
    manifest=json.load(tar.extractfile('StudyCenter-Public/BUILD-MANIFEST.json'));self.assertEqual(manifest['version'],'test-version');self.assertEqual(manifest['files']['StudyCenter-Public']['size'],17)
 def test_manifest_refuses_data_profiles_credentials(self):
  self.assertIsNotNone(importlib.util.find_spec('scripts.build_release'),'Reproducible packaging missing')
  from scripts.build_release import write_manifest
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/'browser-profiles').mkdir();(root/'browser-profiles'/'cookie.txt').write_text('synthetic excluded data')
   with self.assertRaises(ValueError):write_manifest(root,'test-version')
 @unittest.skipUnless(sys.platform=='linux','APT configuration behavior requires Linux')
 def test_isolated_apt_ignores_host_and_inherited_hooks(self):
  from scripts.build_linux_ocr import isolated_apt
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);malicious=root/'inherited.conf';malicious.write_text('APT::Update::Post-Invoke { "touch SHOULD_NOT_RUN"; };\n')
   original=os.environ.get('APT_CONFIG');os.environ['APT_CONFIG']=str(malicious)
   try:options,environment=isolated_apt(root/'isolated')
   finally:
    if original is None:os.environ.pop('APT_CONFIG',None)
    else:os.environ['APT_CONFIG']=original
   actual=subprocess.check_output(['apt-config',*options,'dump'],env=environment,text=True)
   self.assertNotIn('SHOULD_NOT_RUN',actual);self.assertNotIn('Post-Invoke',actual);self.assertNotIn('/etc/apt/apt.conf.d',actual)
 def test_portable_windows_package_metadata_binds_published_checksums(self):
  self.assertIsNotNone(importlib.util.find_spec('scripts.build_windows_ocr'),'Portable archive provenance missing')
  from scripts.build_windows_ocr import read_database
  import io
  with tempfile.TemporaryDirectory() as tmp:
   database=pathlib.Path(tmp)/'repo.db'
   with tarfile.open(database,'w:gz') as archive:
    value=b'%NAME%\nsynthetic-ocr\n\n%VERSION%\n1.0-1\n\n%FILENAME%\nsynthetic-ocr.pkg.tar.zst\n\n%SHA256SUM%\n' + b'a'*64 + b'\n\n%CSIZE%\n123\n\n%DEPENDS%\nsynthetic-lib>=1\n'
    info=tarfile.TarInfo('synthetic-ocr-1.0-1/desc');info.size=len(value);archive.addfile(info,io.BytesIO(value))
   records=read_database(database)
   self.assertEqual(records['synthetic-ocr']['sha256'],'a'*64);self.assertEqual(records['synthetic-ocr']['depends'],['synthetic-lib>=1'])
 def test_frozen_code_audit_checks_nested_filenames_and_constants(self):
  self.assertIsNotNone(importlib.util.find_spec('scripts.package_audit'),'Frozen code provenance audit missing')
  from scripts.package_audit import inspect_code
  synthetic_home='/'+ 'home' + '/synthetic-person/module.py'
  unsafe=compile('def nested():\n return "safe"\n',synthetic_home,'exec')
  self.assertTrue(any(finding['rule']=='personal path' for finding in inspect_code(unsafe,'synthetic-module')))
  safe=compile('def nested():\n return "synthetic content"\n','studycenter_public/module.py','exec')
  self.assertEqual(inspect_code(safe,'synthetic-module'),[])
 def test_installer_probe_refuses_productive_host_before_any_work(self):
  from unittest.mock import patch
  from scripts.smoke_installer_vm import require_disposable_runner
  with patch.dict(os.environ,{'GITHUB_ACTIONS':'false','RUNNER_ENVIRONMENT':'self-hosted'},clear=False):
   with self.assertRaisesRegex(RuntimeError,'ephemeral'):require_disposable_runner()
 def test_audit_review_requires_exact_module_location_rule_and_digest(self):
  from scripts.package_audit import classify_findings,REVIEWED_CONSTANTS
  known=next(iter(REVIEWED_CONSTANTS));module,location,rule,digest=known
  finding={'module':module,'location':location,'rule':rule,'sha256':digest}
  result=classify_findings([finding]);self.assertEqual(result['findings'],[]);self.assertEqual(len(result['reviewed_findings']),1)
  for field in ('module','location','rule','sha256'):
   changed=dict(finding);changed[field]+='changed'
   self.assertEqual(classify_findings([changed])['findings'],[changed])
 def test_bundle_excludes_collected_build_cache_and_browser_setup(self):
  from scripts.build_release import strip_build_artifacts
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);cache=root/'_internal/vendor/__pycache__';cache.mkdir(parents=True);(cache/'synthetic.pyc').write_bytes(b'synthetic')
   browser=root/'components/browsers/chromium-synthetic/chrome-win64';browser.mkdir(parents=True);(browser/'setup.exe').write_bytes(b'synthetic setup');(browser/'chrome.exe').write_bytes(b'synthetic runtime')
   strip_build_artifacts(root)
   self.assertFalse(cache.exists());self.assertFalse((browser/'setup.exe').exists());self.assertTrue((browser/'chrome.exe').exists())
 def test_source_manifest_rejects_wrong_checkpoint(self):
  from scripts.build_release import write_manifest
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaisesRegex(ValueError,'commit'):write_manifest(pathlib.Path(tmp),'synthetic','0'*40)
 def test_source_inputs_changed_during_freeze_fail_closed(self):
  from scripts.build_release import file_hash,ensure_source_inputs_unchanged
  with tempfile.TemporaryDirectory() as tmp:
   source=pathlib.Path(tmp)/'synthetic.py';source.write_text('synthetic original')
   snapshot={source:file_hash(source)};ensure_source_inputs_unchanged(snapshot)
   source.write_text('synthetic later version')
   with self.assertRaisesRegex(ValueError,'changed'):ensure_source_inputs_unchanged(snapshot)
if __name__=='__main__':unittest.main()
