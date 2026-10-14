import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest


def synthetic_pe(import_name=None, exports=(), machine=0x8664):
    data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
    data[128:132]=b'PE\0\0';struct.pack_into('<HHIIIHH',data,132,machine,1,0,0,0,240,0x2022)
    struct.pack_into('<H',data,152,0x20b);struct.pack_into('<I',data,260,16)
    section=392;data[section:section+8]=b'.rdata\0\0';struct.pack_into('<IIII',data,section+8,1536,4096,1536,512)
    if import_name:
        struct.pack_into('<II',data,272,4096,40)
        struct.pack_into('<IIIII',data,512,4160,0,0,4192,4160)
        struct.pack_into('<Q',data,576,4256);data[608:608+len(import_name)+1]=import_name.encode()+b'\0'
        data[674:684]=b'synthetic\0'
    if exports:
        struct.pack_into('<II',data,264,4096,256)
        struct.pack_into('<IIIIII',data,528,1,len(exports),len(exports),4160,4224,4288)
        for i,name in enumerate(exports):
            struct.pack_into('<I',data,576+4*i,4608)
            struct.pack_into('<I',data,640+4*i,4352+i*64)
            struct.pack_into('<H',data,704+2*i,i)
            encoded=name.encode()+b'\0';data[768+i*64:768+i*64+len(encoded)]=encoded
    return bytes(data)


@unittest.skipUnless(sys.platform=='win32','Windows PE build/runtime prerequisite checks')
class SystemRuntimePackageTest(unittest.TestCase):
    def test_system_payload_exclusions_cover_alias_api_forwarders_and_sdk(self):
        from scripts.package_windows_runtime import omitted_system_binary
        for name in ('MSVCP140.dll','msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll','api-ms-win-core-file-l1-1-0.dll','api-ms-win-crt-runtime-l1-1-0.dll','D3DCompiler_47.dll'):
            self.assertTrue(omitted_system_binary(name))
        for name in ('openblas.dll','dxcompiler.dll','WebView2Loader.dll'):
            self.assertFalse(omitted_system_binary(name))

    def test_exact_primary_consumer_patch_preserves_every_other_byte(self):
        from scripts.package_windows_runtime import rewrite_aliases
        name='msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll';original=synthetic_pe(name)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.pyd';path.write_bytes(original)
            recipe=rewrite_aliases(path,{hashlib.sha256(original).hexdigest():'synthetic-primary-member.pyd'})
            edited=path.read_bytes();offset=recipe['edits'][0]['offset']
            self.assertEqual(edited[:offset],original[:offset]);self.assertEqual(edited[offset+len(name):],original[offset+len(name):])
            self.assertEqual(edited[offset:offset+12],b'msvcp140.dll')
            self.assertEqual(recipe['patched_sha256'],hashlib.sha256(edited).hexdigest())

    def test_unverified_or_unknown_mangled_consumer_refuses_before_write(self):
        from scripts.package_windows_runtime import rewrite_aliases
        for name in ('msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll','msvcp140-0123456789abcdef.dll'):
            original=synthetic_pe(name)
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'synthetic.pyd';path.write_bytes(original)
                with self.assertRaises(ValueError):rewrite_aliases(path,{})
                self.assertEqual(path.read_bytes(),original)

    def test_system_preflight_requires_x64_and_actual_exports_without_loading(self):
        from scripts.package_windows_runtime import check_system
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'msvcp140.dll';requirements={'msvcp140.dll':['required']}
            with self.assertRaisesRegex(RuntimeError,'missing'):check_system(requirements,root)
            path.write_bytes(synthetic_pe(exports=['required'],machine=0x14c))
            with self.assertRaisesRegex(RuntimeError,'x64'):check_system(requirements,root)
            path.write_bytes(synthetic_pe(exports=['other']))
            with self.assertRaisesRegex(RuntimeError,'exports'):check_system(requirements,root)
            path.write_bytes(synthetic_pe(exports=['required']));self.assertTrue(check_system(requirements,root))

    def test_package_omits_system_bytes_but_keeps_owned_shader_and_proves_patch(self):
        from scripts.package_windows_runtime import prepare
        from unittest.mock import patch
        alias='msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll'
        consumer=synthetic_pe(alias)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=root/'bundle';bundle.mkdir()
            for name in ('vcruntime140.dll','ucrtbase.dll','api-ms-win-core-file-l1-1-0.dll','api-ms-win-crt-runtime-l1-1-0.dll','D3DCompiler_47.dll',alias):
                (bundle/name).write_bytes(synthetic_pe())
            (bundle/'dxcompiler.dll').write_bytes(synthetic_pe());(bundle/'consumer.pyd').write_bytes(consumer)
            inputs=({hashlib.sha256(consumer).hexdigest():'synthetic.pyd'},{'origin':'synthetic primary fixture'})
            with patch('scripts.package_windows_runtime.primary_numpy_members',return_value=inputs):recipe=prepare(bundle,root/'downloads')
            self.assertEqual(len(recipe['omitted_files']),6)
            self.assertTrue((bundle/'dxcompiler.dll').exists());self.assertEqual(len(recipe['patched_consumers']),1)
            self.assertIn('msvcp140.dll',recipe['system_requirements'])
            self.assertNotIn(alias.lower(),recipe['system_requirements'])

    def test_signed_primary_consumer_is_refused_without_mutation(self):
        from scripts.package_windows_runtime import rewrite_aliases
        original=bytearray(synthetic_pe('msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll'))
        struct.pack_into('<II',original,296,1536,16)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'signed-synthetic.pyd';path.write_bytes(original)
            with self.assertRaisesRegex(ValueError,'signed'):rewrite_aliases(path,{hashlib.sha256(original).hexdigest():'synthetic.pyd'})
            self.assertEqual(path.read_bytes(),original)

    def test_ucrt_api_set_imports_become_concrete_system_ucrt_export_checks(self):
        from scripts.package_windows_runtime import prepare
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=root/'bundle';bundle.mkdir()
            (bundle/'synthetic-consumer.pyd').write_bytes(synthetic_pe('api-ms-win-crt-heap-l1-1-0.dll'))
            with patch('scripts.package_windows_runtime.primary_numpy_members',return_value=({},{})):
                recipe=prepare(bundle,root/'downloads')
            self.assertEqual(recipe['system_requirements']['ucrtbase.dll'],['synthetic'])

    def test_installer_checks_system_requirement_before_target_creation(self):
        script=(Path(__file__).parents[1]/'packaging/windows/installer.iss').read_text()
        prepare=script.split('function PrepareToInstall',1)[1].split('function InitializeUninstall',1)[0]
        self.assertLess(prepare.index('SystemRuntimeProblem'),prepare.index('DataDirectoryProblem'))


if __name__=='__main__':unittest.main()
