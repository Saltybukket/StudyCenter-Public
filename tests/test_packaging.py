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
   unused=root/'components/browsers/ffmpeg-synthetic';unused.mkdir();(unused/'synthetic').write_bytes(b'unused video helper')
   drm=browser/'WidevineCdm';drm.mkdir();(drm/'synthetic').write_bytes(b'unused DRM helper')
   strip_build_artifacts(root)
   self.assertFalse(cache.exists());self.assertFalse((browser/'setup.exe').exists());self.assertTrue((browser/'chrome.exe').exists());self.assertFalse(unused.exists());self.assertFalse(drm.exists())
 def test_source_manifest_rejects_wrong_checkpoint(self):
  from scripts.build_release import write_manifest
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaisesRegex(ValueError,'commit'):write_manifest(pathlib.Path(tmp),'synthetic','0'*40)
 def test_source_inputs_changed_during_freeze_fail_closed(self):
  from scripts.build_release import file_hash,ensure_source_inputs_unchanged
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   source=pathlib.Path(tmp)/'synthetic.py';source.write_text('synthetic original')
   snapshot={source:file_hash(source)}
   with patch('scripts.build_release.source_members',return_value=[source],create=True):
    ensure_source_inputs_unchanged(snapshot)
    source.write_text('synthetic later version')
    with self.assertRaisesRegex(ValueError,'changed'):ensure_source_inputs_unchanged(snapshot)
 def test_actual_package_definition_change_during_freeze_blocks_candidate(self):
  from scripts.build_release import source_inputs,ensure_source_inputs_unchanged
  from unittest.mock import patch
  public=pathlib.Path(__file__).parents[1]
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/'src/studycenter_public').mkdir(parents=True)
   for directory in ('packaging','scripts'):
    import shutil
    shutil.copytree(public/directory,root/directory,ignore=shutil.ignore_patterns('__pycache__'))
   for name in ('README.md','LICENSE','pyproject.toml','requirements-windows.lock','requirements-linux.lock'):(root/name).write_bytes((public/name).read_bytes())
   with patch('scripts.build_release.ROOT',root),patch('scripts.build_release.NOTICE_COMPLETION_FILE',root/'packaging/component-notices.json'):
    snapshot=source_inputs(None);ensure_source_inputs_unchanged(snapshot)
    definition=root/'packaging/windows/installer.iss'
    self.assertIn(definition,snapshot)
    definition.write_bytes(definition.read_bytes()+b'\n; synthetic later packaging change\n')
    with self.assertRaisesRegex(ValueError,'changed'):ensure_source_inputs_unchanged(snapshot)
 def test_source_member_added_after_snapshot_blocks_candidate(self):
  from scripts.build_release import source_inputs,ensure_source_inputs_unchanged,PACKAGE_DEFINITIONS
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);source=root/'src/studycenter_public';source.mkdir(parents=True)
   (source/'original.py').write_text('synthetic original')
   for name in PACKAGE_DEFINITIONS:
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic definition')
   with patch('scripts.build_release.ROOT',root):
    snapshot=source_inputs(None);ensure_source_inputs_unchanged(snapshot)
    added=source/'later.py';added.write_text('synthetic later source')
    with self.assertRaisesRegex(ValueError,'changed'):ensure_source_inputs_unchanged(snapshot)
    added.unlink();ensure_source_inputs_unchanged(snapshot)
    (source/'original.py').unlink()
    with self.assertRaisesRegex(ValueError,'changed'):ensure_source_inputs_unchanged(snapshot)
 def test_linux_helper_stages_are_new_and_recipe_enters_bundle(self):
  from scripts.build_release import stage_linux_desktop
  from unittest.mock import patch
  staged=[]
  def compile_fixture(output,compiler):
   staged.append(output)
   for name in ('desktop-install','install-desktop.sh','DESKTOP-INSTALL-RECIPE.json'):(output/name).write_bytes(b'Synthetic bounded helper artifact')
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);bundle=root/'bundle';bundle.mkdir()
   with patch('scripts.build_release.ROOT',root),patch('scripts.build_release.shutil.which',return_value=sys.executable),patch('scripts.package_linux_desktop.prepare',side_effect=compile_fixture):
    stage_linux_desktop(bundle,{'PATH':os.environ['PATH']})
    first={name:(staged[0]/name).read_bytes() for name in ('desktop-install','install-desktop.sh','DESKTOP-INSTALL-RECIPE.json')}
    stage_linux_desktop(bundle,{'PATH':os.environ['PATH']})
   self.assertNotEqual(staged[0],staged[1])
   self.assertEqual({name:(staged[0]/name).read_bytes() for name in first},first)
   if sys.platform=='linux':self.assertTrue((bundle/'desktop-install').stat().st_mode & 0o111)
   self.assertTrue((bundle/'DESKTOP-INSTALL-RECIPE.json').is_file())
 def test_snapshot_extraction_refuses_escape_and_keeps_neighbor(self):
  from scripts.build_browser_runtime import extract_snapshot
  import zipfile
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);neighbor=root/'neighbor.txt';neighbor.write_text('synthetic preserved');archive=root/'snapshot.zip'
   with zipfile.ZipFile(archive,'w') as output:output.writestr('chrome-win/../../neighbor.txt','synthetic forbidden')
   with self.assertRaisesRegex(ValueError,'Unsafe'):extract_snapshot(archive,root/'runtime','chrome-win','windows')
   self.assertEqual(neighbor.read_text(),'synthetic preserved')
 def test_frozen_browser_diagnostics_require_sandbox(self):
  from types import SimpleNamespace
  specification=importlib.util.spec_from_file_location('frozen_diagnostic_fixture',pathlib.Path(__file__).parents[1]/'packaging/frozen_entry.py')
  module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module)
  with tempfile.TemporaryDirectory() as tmp:
   for channel in (None,'chromium'):
    options=module.browser_launch_options(channel,SimpleNamespace(cache=pathlib.Path(tmp)))
    self.assertIs(options['chromium_sandbox'],True)
    self.assertNotIn('--no-sandbox',options.get('args',[]))
   if sys.platform=='win32':
    with self.assertRaises(ValueError):module.browser_launch_options(None)
 def test_frozen_browser_logs_and_xdg_caches_are_owned_child_options(self):
  from types import SimpleNamespace
  specification=importlib.util.spec_from_file_location('frozen_environment_fixture',pathlib.Path(__file__).parents[1]/'packaging/frozen_entry.py')
  module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module)
  with tempfile.TemporaryDirectory() as tmp:
   cache=pathlib.Path(tmp);before=dict(os.environ)
   options=module.browser_launch_options('chromium',SimpleNamespace(cache=cache))
   self.assertEqual(dict(os.environ),before)
   for key in ('CHROME_LOG_FILE','XDG_CONFIG_HOME','XDG_CACHE_HOME'):
    self.assertTrue(pathlib.Path(options['env'][key]).is_relative_to(cache))
 def test_real_screenshot_probe_requires_sandbox_in_both_channels(self):
  from scripts.smoke_release import sandbox_screenshot_script
  for channel in (None,'chromium'):
   script=sandbox_screenshot_script('synthetic/package','synthetic.png',channel)
   self.assertIn('chromiumSandbox:true',script);self.assertNotIn('--no-sandbox',script);self.assertNotIn('cli.js',script)
   if channel is None and sys.platform!='win32':self.assertNotIn('channel:',script)
 def test_installer_preflight_checks_identity_and_ancestors_before_mutation(self):
  script=(pathlib.Path(__file__).parents[1]/'packaging/windows/installer.iss').read_text()
  self.assertNotIn('Assigned(DataDirectoryPage)',script)
  for required in ('GetFileAttributesW','StrictPublicIdentity','ProgramDirectoryProblem','installation-files.txt','GetSHA256OfFile'):
   self.assertIn(required,script)
  prepare=script.split('function PrepareToInstall',1)[1].split('function InitializeUninstall',1)[0]
  self.assertLess(prepare.index('ProgramDirectoryProblem'),prepare.index('Exec('))
  self.assertLess(prepare.index('DataDirectoryProblem'),prepare.index('Exec('))
  data=script.split('function DataDirectoryProblem',1)[1].split('function PrepareToInstall',1)[0]
  self.assertLess(data.index('StrictPublicIdentity'),data.index('ForceDirectories('))
  uninstall=script.split('function InitializeUninstall',1)[1]
  self.assertLess(uninstall.index('ProgramDirectoryProblem'),uninstall.index('Exec('))
 def test_installer_enumeration_captures_native_errors_and_fails_closed(self):
  script=(pathlib.Path(__file__).parents[1]/'packaging/windows/installer.iss').read_text()
  self.assertIn("FindFirstFileW@kernel32.dll",script);self.assertIn("FindNextFileW@kernel32.dll",script)
  self.assertIn('Search.Error := DLLGetLastError;',script)
  self.assertIn('if Search.Error <> 18 then exit;',script)
  self.assertIn('(Search.Error <> 2) and (Search.Error <> 18) then Result := True',script)
 def test_native_builder_path_excludes_inherited_development_directories(self):
  from scripts.build_release import build_environment
  from unittest.mock import patch
  with patch.dict(os.environ,{'PATH':'synthetic-foreign-development-runtime'},clear=False):
   environment=build_environment('windows')
  self.assertNotIn('synthetic-foreign',environment['PATH'])
  self.assertIn(str(pathlib.Path(sys.executable).parent),environment['PATH'])
 def test_collected_native_dependency_from_foreign_directory_blocks_package(self):
  from scripts.build_release import verify_native_origins
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);own=root/'own';own.mkdir();foreign=root/'unrelated';foreign.mkdir();toc=root/'COLLECT-00.toc'
   toc.write_text(repr([('native.dll',str(own/'native.dll'),'BINARY')]))
   verify_native_origins(toc,[own])
   toc.write_text(repr([('native.dll',str(foreign/'native.dll'),'BINARY')]))
   with self.assertRaisesRegex(ValueError,'unapproved'):verify_native_origins(toc,[own])
 def test_silent_installer_defers_refusal_to_mandatory_engine_preflight(self):
  script=(pathlib.Path(__file__).parents[1]/'packaging/windows/installer.iss').read_text()
  next_page=script.split('function NextButtonClick',1)[1].split('function PrepareToInstall',1)[0]
  self.assertLess(next_page.index('if WizardSilent then exit;'),next_page.index('ProgramDirectoryProblem'))
  self.assertNotRegex(script,r'\bMsgBox\(')
 def test_installer_timestamp_arguments_are_pascal_characters(self):
  script=(pathlib.Path(__file__).parents[1]/'packaging/windows/installer.iss').read_text()
  self.assertEqual(script.count("GetDateTimeString('yyyymmddhhnnsszzz', #0, #0)"),2)
  self.assertNotIn("GetDateTimeString('yyyymmddhhnnsszzz', '', '')",script)
 def test_system_export_lookup_is_bounded_binary_search(self):
  script=(pathlib.Path(__file__).parents[1]/'packaging/windows/system_runtime.iss').read_text()
  self.assertIn('while Low <= High do begin',script)
  self.assertIn('RuntimeCompareASCII(ExportName, Symbol)',script)
  self.assertIn('OrdinalIndex := RuntimeU16(Image, Ordinals + Middle * 2)',script)
  self.assertIn('System runtime prerequisite: complete',script)
 def test_core_only_smoke_keeps_browser_and_speech_unaccepted(self):
  from scripts.smoke_release import run_browser_checks
  from unittest.mock import patch
  with patch('scripts.smoke_release.subprocess.run',side_effect=AssertionError('Browser must not start')):
   result=run_browser_checks(pathlib.Path('synthetic/StudyCenter-Public'),pathlib.Path('synthetic/output'),{},False)
  self.assertEqual(result['bundled_driver_and_required_browsers'],'not_run')
  self.assertEqual(result['bundled_screenshots_without_video_helper'],'not_run')
  self.assertEqual(result['native_speech_imports_cpu'],'not_run')
 def test_linux_smoke_child_resolves_only_bundled_native_libraries_and_own_caches(self):
  from scripts.smoke_release import smoke
  from unittest.mock import patch
  class StopProbe(Exception):pass
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp).resolve();executable=root/'bundle/StudyCenter-Public';output=root/'synthetic-diagnostic'
   executable.parent.mkdir();executable.write_bytes(b'Synthetic executable; mocked native command prevents execution')
   (executable.parent/'BUILD-MANIFEST.json').write_text(__import__('json').dumps({'files':{'StudyCenter-Public':{'size':executable.stat().st_size,'sha256':__import__('hashlib').sha256(executable.read_bytes()).hexdigest()}}}))
   captured={}
   def inspect(command,**options):captured.update(options['env']);raise StopProbe()
   with patch('scripts.smoke_release.sys.platform','linux'),patch('scripts.smoke_release.subprocess.run',side_effect=inspect):
    with self.assertRaises(StopProbe):smoke(executable,output)
   self.assertEqual(captured['LD_LIBRARY_PATH'],str(executable.parent/'components/ocr/usr/lib/x86_64-linux-gnu'))
   for key in ('CHROME_LOG_FILE','XDG_CONFIG_HOME','XDG_CACHE_HOME'):
    self.assertTrue(pathlib.Path(captured[key]).is_relative_to(output))
 def test_binary_notices_include_exact_source_stage_without_diagnostic_helpers(self):
  from scripts.build_release import copy_license_materials
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);materials=root/'materials';notice=materials/'webview/LICENSE.txt';notice.parent.mkdir(parents=True);notice.write_bytes(b'Synthetic complete binary redistribution notice')
   (materials/'capture-own-browser-credits.js').write_bytes(b'Synthetic private diagnostic helper')
   (materials/'browser.log').write_bytes(b'Synthetic private log')
   excluded=materials/'microsoft-vc-runtime';excluded.mkdir();(excluded/'terms.txt').write_bytes(b'Synthetic excluded CRT entitlement material')
   bundle=root/'bundle';bundle.mkdir();receipt=copy_license_materials(bundle,materials)
   self.assertEqual((bundle/'licenses/component-notices/webview/LICENSE.txt').read_bytes(),notice.read_bytes())
   self.assertEqual(receipt['files']['webview/LICENSE.txt']['sha256'],__import__('hashlib').sha256(notice.read_bytes()).hexdigest())
   self.assertFalse((bundle/'licenses/component-notices/capture-own-browser-credits.js').exists())
   self.assertFalse((bundle/'licenses/component-notices/browser.log').exists())
   self.assertFalse((bundle/'licenses/component-notices/microsoft-vc-runtime').exists())
 def test_missing_notices_diagnostic_is_explicitly_not_publishable(self):
  from scripts.build_release import copy_license_materials,validate_component_notices
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);bundle=root/'bundle';bundle.mkdir()
   with self.assertRaisesRegex(ValueError,'required'):copy_license_materials(bundle,root/'absent')
   receipt=copy_license_materials(bundle,root/'absent',allow_missing=True)
   self.assertFalse(receipt['complete_stage_present']);self.assertIn('publication_blocked',receipt)
   with self.assertRaisesRegex(ValueError,'cannot be promoted'):validate_component_notices(bundle)
 def test_partial_late_component_notices_cannot_pass_completion_gate(self):
  from scripts.build_release import copy_license_materials,validate_component_notices
  from unittest.mock import patch
  import json,hashlib
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);materials=root/'materials';materials.mkdir();(materials/'NOTICE-PROVENANCE.json').write_text('[]')
   texts=materials/'license-texts';texts.mkdir()
   for name in ('AGPL-3.0-only.txt','COPYING.GPLv2','COPYING.GPLv3','COPYING.LGPLv2.1','LGPL-3.0-only.txt','MPL-2.0.txt'):(texts/name).write_text('Synthetic license fixture')
   index=root/'completion.json';index.write_text(json.dumps({'files':{'webview/NOTICE.txt':{'sha256':hashlib.sha256(b'Synthetic required late notice').hexdigest(),'size':28}}}))
   bundle=root/'bundle';bundle.mkdir();copy_license_materials(bundle,materials)
   with patch('scripts.build_release.NOTICE_COMPLETION_FILE',index,create=True):
    with self.assertRaisesRegex(ValueError,'completion'):validate_component_notices(bundle)
if __name__=='__main__':unittest.main()


def test_browser_stage_refuses_nonempty_runtime_without_mutation_or_network(tmp_path,monkeypatch):
 from scripts import build_browser_runtime as staging
 import pytest
 output=tmp_path/'output';runtime=output/'runtime';runtime.mkdir(parents=True)
 unknown=runtime/'unknown-owned-fixture.txt';unknown.write_bytes(b'preserve unknown fixture')
 monkeypatch.setattr(staging,'get_json',lambda url:pytest.fail('Network must not precede stale-stage refusal'))
 with pytest.raises(ValueError,match='empty|nonempty'):
  staging.stage('windows',tmp_path/'original',output)
 assert unknown.read_bytes()==b'preserve unknown fixture' and list(runtime.iterdir())==[unknown]


def test_browser_stage_preserves_only_platform_required_shell(tmp_path,monkeypatch):
 import base64,hashlib,io,json,zipfile
 from scripts import build_browser_runtime as staging
 for platform in ('windows','linux'):
  original=tmp_path/platform/'original';shell=original/'chromium_headless_shell-synthetic';shell.mkdir(parents=True);(shell/'synthetic').write_bytes(b'synthetic shell')
  helper=original/'winldd-synthetic';helper.mkdir();(helper/'synthetic').write_bytes(b'synthetic helper')
  output=tmp_path/platform/'output';output.mkdir()
  item=dict(staging.PLATFORMS[platform]);archive=output/item['archive']
  with zipfile.ZipFile(archive,'w') as file:file.writestr(item['folder']+'/synthetic-browser',b'synthetic unbranded full browser')
  data=archive.read_bytes();item.update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
  monkeypatch.setitem(staging.PLATFORMS,platform,item)
  monkeypatch.setattr(staging,'get_json',lambda url:{'got_revision':item['source_git_sha']} if url.endswith('/REVISIONS') else {'size':len(data),'md5Hash':base64.b64encode(hashlib.md5(data).digest()).decode()})
  def license_only(url,**kwargs):
   assert url.endswith('/LICENSE?format=TEXT')
   return io.BytesIO(base64.b64encode(b'Synthetic BSD notice: Redistribution and use in source and binary forms'))
  monkeypatch.setattr(staging.urllib.request,'urlopen',license_only)
  runtime=staging.stage(platform,original,output)
  assert (runtime/shell.name).exists() is (platform=='linux')
  assert (shell/'synthetic').read_bytes()==b'synthetic shell'
  manifest=json.loads((runtime/'UNBRANDED-MANIFEST.json').read_text())
  assert manifest['headless_shell_preserved'] is (platform=='linux')


def test_browser_stage_refuses_linked_output_without_touching_target(tmp_path,monkeypatch):
 from scripts import build_browser_runtime as staging
 import pytest
 foreign=tmp_path/'other-fixture';foreign.mkdir();marker=foreign/'preserved.txt';marker.write_bytes(b'unchanged')
 link=tmp_path/'linked-output'
 try:link.symlink_to(foreign,target_is_directory=True)
 except (OSError,NotImplementedError):pytest.skip('This account cannot create an own directory symlink')
 monkeypatch.setattr(staging,'get_json',lambda url:pytest.fail('No network before link refusal'))
 with pytest.raises(ValueError,match='link|reparse'):staging.stage('windows',tmp_path/'original',link)
 assert marker.read_bytes()==b'unchanged' and list(foreign.iterdir())==[marker]


def test_windows_prefreeze_validates_only_shipped_full_engine():
 workflow=(pathlib.Path(__file__).parents[1]/'.github/workflows/build.yml').read_text()
 validation=workflow.split('- name: Validate own Windows browser stage before freezing',1)[1].split('- name: Freeze Windows bundle',1)[0]
 assert 'registry.findExecutable("chromium")' in validation
 assert 'registry.findExecutable("chromium-headless-shell")' not in validation
