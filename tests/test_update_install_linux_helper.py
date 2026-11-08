import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from studycenter_public import update_install as update


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux pidfd required')
class HandoffLinuxTests(unittest.TestCase):
    def test_success_and_failure_preserve_data_and_wait_for_owned_process(self):
        for succeeds in (True, False, None):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory(prefix='sc-update-test-') as directory:
                base=Path(directory); root=base/'app'; root.mkdir(); local=root/'.local'; local.mkdir()
                data=base/'data'; data.mkdir(); (data/'original').write_text('private sentinel')
                stage=base/'.studycenter-update-test'; stage.mkdir(); release=stage/'release'; release.mkdir()
                old={name: ('old '+name).encode() for name in ('start.sh','LICENSE','README.md','setup.py','backup_core.py','slim-manifest.json')}
                for name,value in old.items(): (root/name).write_bytes(value)
                for name in old: (release/name).write_text('new '+name)
                (release/'setup.py').write_text('import sys\nsys.exit('+('0' if succeeds else '7')+')\n')
                receipt={'python':sys.executable,'uv':'unused'}
                (local/'installation.json').write_text(json.dumps(receipt)); (local/'python-path.txt').write_text(sys.executable)
                artifact=stage/'release.tar.gz'; artifact.write_bytes(b'synthetic archive')
                sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
                plan={'root':str(root),'stage':str(stage),'artifact':str(artifact),'sha256':sha(artifact),
                      'files':{p.name:sha(p) for p in release.iterdir()},'data':str(data),'explicit':False,'port':1,'receipt':receipt}
                (stage/'handoff.json').write_text(json.dumps(plan)); (stage/'helper.py').write_text(update._LINUX_HELPER); (stage/'recover.py').write_text(update._RECOVERY_HELPER)
                owned=subprocess.Popen(['/bin/sleep','30']); fd=os.pidfd_open(owned.pid)
                helper=subprocess.Popen([sys.executable,'-I',str(stage/'helper.py'),str(stage/'handoff.json'),str(fd)],pass_fds=(fd,)); os.close(fd)
                try:
                    deadline=time.monotonic()+10
                    while not (stage/'ready').exists() and helper.poll() is None and time.monotonic()<deadline: time.sleep(.05)
                    self.assertTrue((stage/'ready').exists())
                    self.assertEqual((root/'setup.py').read_bytes(),old['setup.py'])
                    (stage/'commit').write_text('commit')
                    deadline=time.monotonic()+5
                    while not (stage/'accepted').exists() and time.monotonic()<deadline: time.sleep(.05)
                    self.assertTrue((stage/'accepted').exists())
                    if succeeds is not None: (stage/'go').write_text('go')
                    time.sleep(.2)
                    self.assertEqual((root/'setup.py').read_bytes(),old['setup.py'])
                    owned.terminate(); owned.wait(timeout=5)
                    helper.wait(timeout=15)
                    outcome=json.loads((stage/'outcome.json').read_text())
                    self.assertEqual(outcome['status'], 'aborted' if succeeds is None else ('installed' if succeeds else 'failed'))
                    self.assertEqual((root/'setup.py').read_bytes(),(release/'setup.py').read_bytes() if succeeds is True else old['setup.py'])
                    self.assertEqual((data/'original').read_text(),'private sentinel')
                    self.assertFalse((local/'update-journal.json').exists())
                finally:
                    if helper.poll() is None: helper.terminate(); helper.wait(timeout=5)
                    if owned.poll() is None: owned.terminate(); owned.wait(timeout=5)

    def test_recovery_rejects_corruption_then_restores_old_files(self):
        with tempfile.TemporaryDirectory(prefix='sc-update-recovery-') as directory:
            base=Path(directory); root=base/'app'; root.mkdir(); local=root/'.local'; local.mkdir()
            stage=base/'.studycenter-update-test'; old=stage/'previous'; old.mkdir(parents=True)
            names=('start.sh','LICENSE','README.md','setup.py','backup_core.py','slim-manifest.json','installation.json','python-path.txt')
            for name in names:
                (old/name).write_text('old '+name)
                target=(local/name) if name in ('installation.json','python-path.txt') else (root/name)
                target.write_text('new '+name)
            journal=local/'update-journal.json'; journal.write_text(json.dumps({'stage':str(stage),'hashes':{name:hashlib.sha256((old/name).read_bytes()).hexdigest() for name in names}}))
            script=base/'recover.py'; script.write_text(update._RECOVERY_HELPER)
            (old/'setup.py').write_text('broken')
            result=subprocess.run([sys.executable,'-I',str(script),str(root)],capture_output=True)
            self.assertNotEqual(result.returncode,0); self.assertTrue(journal.exists()); self.assertEqual((root/'setup.py').read_text(),'new setup.py')
            (old/'setup.py').write_text('old setup.py')
            subprocess.run([sys.executable,'-I',str(script),str(root)],check=True)
            self.assertFalse(journal.exists()); self.assertEqual((root/'setup.py').read_text(),'old setup.py')
            self.assertEqual((local/'installation.json').read_text(),'old installation.json')

    def test_recovery_interruption_at_every_replacement(self):
        for stop_at in range(1, 9):
            with self.subTest(stop_at=stop_at), tempfile.TemporaryDirectory(prefix='sc-update-crash-') as directory:
                base=Path(directory); root=base/'app'; root.mkdir(); local=root/'.local'; local.mkdir()
                stage=base/'.studycenter-update-test'; old=stage/'previous'; old.mkdir(parents=True)
                names=('start.sh','LICENSE','README.md','setup.py','backup_core.py','slim-manifest.json','installation.json','python-path.txt')
                for name in names:
                    (old/name).write_text('old '+name)
                    target=(local/name) if name in ('installation.json','python-path.txt') else (root/name)
                    target.write_text('new '+name)
                journal=local/'update-journal.json'; journal.write_text(json.dumps({'stage':str(stage),'hashes':{name:hashlib.sha256((old/name).read_bytes()).hexdigest() for name in names}}))
                script=base/'recover.py'; script.write_text(update._RECOVERY_HELPER)
                driver=base/'crash.py'; driver.write_text("import os,runpy,sys\nm=runpy.run_path(sys.argv[1]); original=os.replace; count=0\ndef replace(a,b):\n global count\n count+=1\n if count==int(sys.argv[3]): os._exit(99)\n original(a,b)\nos.replace=replace\nm['restore'](sys.argv[2])\n")
                result=subprocess.run([sys.executable,'-I',str(driver),str(script),str(root),str(stop_at)])
                self.assertEqual(result.returncode,99)
                if journal.exists():
                    self.assertEqual((root/'start.sh').read_text(),'new start.sh')
                    subprocess.run([sys.executable,'-I',str(script),str(root)],check=True)
                self.assertFalse(journal.exists())
                for name in names:
                    if name == 'start.sh': continue
                    target=(local/name) if name in ('installation.json','python-path.txt') else (root/name)
                    self.assertEqual(target.read_text(),'old '+name)


if __name__ == '__main__': unittest.main()
