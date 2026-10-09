"""Controlled Git/checksum regressions, separate from media/engine acceptance."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import build_mac_app,managed_shotcut as managed


class ManagedShotcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='filmeditor-patch-test-')
        self.root=Path(self.tmp.name).resolve()
        self.bridge=self.root/'.deps/shotcut-mcp';self.bridge.mkdir(parents=True)
        self.patches=self.root/'patches';self.patches.mkdir()
        self.git('init','--quiet')
        self.original={name:('Pinned fixture: '+name+'\n').encode() for name in sorted(managed.MANAGED_FILES)}
        self.write(self.original)
        (self.bridge/'README.md').write_text('Unrelated pinned content\n')
        self.git('add','.')
        self.git('-c','user.name=Fixture','-c','user.email=fixture@invalid','-c','commit.gpgsign=false','commit','--quiet','-m','Fixture pin')
        self.pin=self.git('rev-parse','HEAD').decode().strip()
        self.legacy={name:data+b'Preserved still duration fix\n' for name,data in self.original.items()}
        self.final={name:data+b'Positive probe classification\n' for name,data in self.legacy.items()}
        self.write(self.legacy)
        (self.patches/'legacy.patch').write_bytes(self.diff())
        self.write(self.final)
        (self.patches/'final.patch').write_bytes(self.diff())
        digest=lambda data:hashlib.sha256(data).hexdigest()
        definition={'version':1,'commit':self.pin,'patch':'final.patch','legacy_patch':'legacy.patch',
                    'patch_sha256':digest((self.patches/'final.patch').read_bytes()),
                    'legacy_patch_sha256':digest((self.patches/'legacy.patch').read_bytes()),
                    'files':{name:{'clean':digest(data),'legacy':digest(self.legacy[name]),'patched':digest(self.final[name])}
                             for name,data in self.original.items()}}
        (self.patches/'shotcut-mcp-stills.json').write_text(json.dumps(definition))
        self.write(self.original)
        self.pinned=patch.object(managed,'SHOTCUT_COMMIT',self.pin);self.pinned.start()

    def tearDown(self):
        self.pinned.stop();self.tmp.cleanup()

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.bridge,stderr=subprocess.PIPE)

    def diff(self):
        return self.git(*managed.DIFF,'HEAD','--')

    def write(self,values):
        for name,data in values.items():
            path=self.bridge/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

    def prepare(self):
        return managed.prepare_bridge(self.bridge,patches=self.patches)

    def verify(self):
        return managed.verify_bridge(self.bridge,patches=self.patches)

    def test_clean_pin_applies_once_and_preserves_pin(self):
        self.assertEqual(self.prepare(),'applied patch')
        self.verify()
        before={name:(self.bridge/name).stat().st_mtime_ns for name in self.final}
        self.assertEqual(self.prepare(),'already patched')
        self.assertEqual(before,{name:(self.bridge/name).stat().st_mtime_ns for name in self.final})
        self.assertEqual(self.git('rev-parse','HEAD').decode().strip(),self.pin)

    def test_exact_staged_legacy_fix_is_upgraded_without_resetting_index(self):
        self.write(self.legacy);self.git('add','.')
        staged=self.git(*managed.DIFF,'--cached','HEAD','--')
        self.assertEqual(self.prepare(),'upgraded preserved still fix')
        self.verify()
        self.assertEqual(self.git(*managed.DIFF,'--cached','HEAD','--'),staged)
        self.assertEqual({name:(self.bridge/name).read_bytes() for name in self.final},self.final)

    def test_unknown_unstaged_and_unrelated_tracked_edits_are_preserved(self):
        self.write(self.legacy)
        for name in ('shotcut_mcp/project_document.py','README.md'):
            with self.subTest(name=name):
                path=self.bridge/name;before=path.read_bytes();path.write_bytes(before+b'Unmanaged user edit\n')
                actual=path.read_bytes()
                with self.assertRaisesRegex(ValueError,'unmanaged edits'):self.prepare()
                self.assertEqual(path.read_bytes(),actual)
                path.write_bytes(before)

    def test_unknown_staged_change_is_preserved_even_when_worktree_matches(self):
        self.write(self.legacy)
        path=self.bridge/'shotcut_mcp/project_document.py'
        path.write_bytes(self.legacy['shotcut_mcp/project_document.py']+b'Unknown staged edit\n')
        self.git('add','.')
        staged=self.git(*managed.DIFF,'--cached','HEAD','--')
        self.write(self.legacy)
        with self.assertRaisesRegex(ValueError,'index has unmanaged edits'):self.prepare()
        self.assertEqual(self.git(*managed.DIFF,'--cached','HEAD','--'),staged)
        self.assertEqual({name:(self.bridge/name).read_bytes() for name in self.legacy},self.legacy)

    def test_untracked_user_file_is_retained(self):
        path=self.bridge/'user-notes.txt';path.write_text('Retain this file')
        with self.assertRaisesRegex(ValueError,'unrelated untracked'):self.prepare()
        self.assertEqual(path.read_text(),'Retain this file')

    def test_different_head_is_rejected_without_source_changes(self):
        self.git('-c','user.name=Fixture','-c','user.email=fixture@invalid','-c','commit.gpgsign=false','commit','--quiet','--allow-empty','-m','Different head')
        with self.assertRaisesRegex(ValueError,'HEAD differs'):self.prepare()
        self.assertEqual({name:(self.bridge/name).read_bytes() for name in self.original},self.original)

    def test_late_publication_failure_restores_exact_legacy_fix(self):
        self.write(self.legacy);self.git('add','.')
        staged=self.git(*managed.DIFF,'--cached','HEAD','--')
        replace=managed._replace;calls=0
        def fail_second(path,data,mode):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('Controlled publication failure')
            return replace(path,data,mode)
        with patch.object(managed,'_replace',side_effect=fail_second):
            with self.assertRaisesRegex(OSError,'publication failure'):self.prepare()
        self.assertEqual({name:(self.bridge/name).read_bytes() for name in self.legacy},self.legacy)
        self.assertEqual(self.git(*managed.DIFF,'--cached','HEAD','--'),staged)

    def test_rollback_does_not_overwrite_a_concurrent_external_edit(self):
        self.write(self.legacy)
        replace=managed._replace;first=None
        def external_change(path,data,mode):
            nonlocal first
            if first is None:
                first=path;return replace(path,data,mode)
            first.write_bytes(b'Concurrent external edit\n')
            raise OSError('Controlled failure after external edit')
        with patch.object(managed,'_replace',side_effect=external_change):
            with self.assertRaisesRegex(OSError,'external edit'):self.prepare()
        self.assertEqual(first.read_bytes(),b'Concurrent external edit\n')

    def test_build_requires_patch_and_rejects_copied_runtime_tampering(self):
        with self.assertRaisesRegex(ValueError,'still patch is missing'):self.verify()
        self.write(self.legacy)
        with self.assertRaisesRegex(ValueError,'still patch is missing'):self.verify()
        self.prepare();manifest=self.verify()
        copy=self.root/'copied-adapter';shutil.copytree(self.bridge,copy,ignore=shutil.ignore_patterns('.git','tests'))
        proof=managed.verify_bundle_bridge(copy,manifest)
        self.assertEqual(proof['commit'],self.pin)
        (copy/'shotcut_mcp/project_document.py').write_bytes(b'Unexpected copied runtime')
        with self.assertRaisesRegex(ValueError,'does not match'):managed.verify_bundle_bridge(copy,manifest)

    def test_native_build_refuses_unverified_adapter_before_compilation(self):
        with patch.object(build_mac_app,'ROOT',self.root),patch.object(build_mac_app,'local_environment',return_value={'FILM_NODE':'/fixture/node'}),\
             patch.object(build_mac_app,'require',side_effect=lambda value,label:str(value)),\
             patch.object(build_mac_app,'command',return_value=SimpleNamespace(stdout='v22.0.0')) as command,\
             patch.object(build_mac_app,'verify_bridge',side_effect=ValueError('Unmanaged adapter')) as verify,\
             patch.object(sys,'platform','darwin'),patch.object(sys,'argv',['build_mac_app.py']):
            with self.assertRaisesRegex(ValueError,'Unmanaged adapter'):build_mac_app.main()
        verify.assert_called_once_with(self.root/'.deps/shotcut-mcp')
        self.assertEqual(len(command.call_args_list),1)
        self.assertEqual(command.call_args.args[0],['/fixture/node','--version'])
        self.assertFalse((self.root/'build').exists())


class PinnedPatchReproductionTests(unittest.TestCase):
    def test_recorded_patch_reconstructs_pinned_dependency_bytes(self):
        bridge=managed.ROOT/'.deps/shotcut-mcp'
        if not (bridge/'.git').exists():self.skipTest('Pinned adapter checkout is not installed')
        if subprocess.check_output(['git','rev-parse','HEAD'],cwd=bridge).decode().strip()!=managed.SHOTCUT_COMMIT:
            self.skipTest('Adapter checkout is at a different revision')
        manifest,_=managed._definition()
        with tempfile.TemporaryDirectory(prefix='filmeditor-pinned-repro-') as directory:
            stage=Path(directory)
            for name,identities in manifest['files'].items():
                original=subprocess.check_output(['git','show','HEAD:'+name],cwd=bridge)
                self.assertEqual(hashlib.sha256(original).hexdigest(),identities['clean'])
                path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(original)
            subprocess.run(['git','apply','--check',str(managed.PATCHES/manifest['patch'])],cwd=stage,check=True,capture_output=True)
            subprocess.run(['git','apply',str(managed.PATCHES/manifest['patch'])],cwd=stage,check=True,capture_output=True)
            for name,identities in manifest['files'].items():
                self.assertEqual(hashlib.sha256((stage/name).read_bytes()).hexdigest(),identities['patched'])


if __name__=='__main__':unittest.main()
