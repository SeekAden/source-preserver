import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('archive', Path(__file__).with_name('update-projects.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class PreservationTests(unittest.TestCase):
    def test_dependency_cleanup_preserves_main_files(self):
        spec = importlib.util.spec_from_file_location('cleanup',Path(__file__).with_name('remove-submodule-snapshots.py'))
        cleanup = importlib.util.module_from_spec(spec); spec.loader.exec_module(cleanup)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'.staging').mkdir()
            snapshot=root/'GitHub'/'owner - repo'/'current'
            source=snapshot/'source'; (source/'externals'/'dependency').mkdir(parents=True)
            (source/'README.md').write_text('main project')
            (source/'LICENSE').write_text('main license')
            (source/'.gitmodules').write_text('[submodule "dependency"]\n path = externals/dependency\n url = https://example.com/owner/dependency\n')
            (source/'externals'/'dependency'/'code.txt').write_text('dependency')
            (snapshot/'metadata.json').write_text(json.dumps({'format':1,'files':a.inventory(source)}))
            cleanup.clean(snapshot)
            self.assertEqual((source/'README.md').read_text(),'main project')
            self.assertEqual((source/'LICENSE').read_text(),'main license')
            self.assertTrue((source/'.gitmodules').exists())
            self.assertFalse((source/'externals'/'dependency').exists())
            self.assertEqual(a.verify(snapshot)['scope'],'main-repository')
            self.assertFalse(snapshot.with_name('.dependency-backup-current').exists())

    def snapshot(self, path, text):
        (path/'source').mkdir(parents=True)
        (path/'source'/'README.md').write_text(text)
        (path/'metadata.json').write_text(json.dumps({'format':1,'files':a.inventory(path/'source'), 'source_url':'https://github.com/owner/repo','commit_sha':text}))

    def test_rotation_keeps_only_two(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root/'repo'; folder.mkdir()
            for n in ('one','two','three'):
                stage = root/n
                self.snapshot(stage,n)
                a.promote(folder,stage)
            self.assertEqual(a.verify(folder/'current')['commit_sha'],'three')
            self.assertEqual(a.verify(folder/'previous')['commit_sha'],'two')
            self.assertEqual(sorted(p.name for p in folder.iterdir()),['current','previous'])

    def test_failed_upstream_preserves_both(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); folder=root/'GitHub'/'owner - repo'
            self.snapshot(folder/'current','one'); self.snapshot(folder/'previous','zero')
            before=a.inventory(folder)
            with patch.object(a,'git',side_effect=RuntimeError('Unavailable')):
                with self.assertRaises(RuntimeError): a.archive(root,'https://github.com/owner/repo')
            self.assertEqual(before,a.inventory(folder))

    def test_local_edits_block_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); folder=root/'repo'; stage=root/'stage'
            self.snapshot(folder/'current','one'); self.snapshot(stage,'two')
            (folder/'current'/'source'/'README.md').write_text('personal edits')
            with self.assertRaises(RuntimeError): a.promote(folder,stage)
            self.assertTrue(stage.exists())

    def test_finder_metadata_does_not_block_source_verification(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.snapshot(root/'current','one')
            (root/'current'/'source'/'.DS_Store').write_bytes(b'Finder metadata')
            a.verify(root/'current')
            (root/'current'/'source'/'personal.txt').write_text('Keep this')
            with self.assertRaises(RuntimeError): a.verify(root/'current')

    def test_lfs_download_does_not_inherit_skip(self):
        import subprocess
        with patch.object(a.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
            a.git('lfs','pull',download_lfs=True)
            self.assertNotIn('GIT_LFS_SKIP_SMUDGE',run.call_args.kwargs['env'])
            a.git('clone','example')
            self.assertEqual(run.call_args.kwargs['env']['GIT_LFS_SKIP_SMUDGE'],'1')

    def test_interrupted_transaction_rolls_back(self):
        for moved in (False,True):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); folder=root/'repo'; stage=root/'stage'
                self.snapshot(folder/'current','one'); self.snapshot(folder/'previous','zero'); self.snapshot(stage,'two')
                (folder/'.transaction.json').write_text(json.dumps({'format':1,'stage':str(stage)}))
                (folder/'previous').rename(folder/'.old-previous')
                if moved: (folder/'current').rename(folder/'.old-current')
                a.recover(folder)
                self.assertEqual(a.verify(folder/'current')['commit_sha'],'one')
                self.assertEqual(a.verify(folder/'previous')['commit_sha'],'zero')

    def test_interrupted_transaction_finishes_committed_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); folder=root/'repo'; stage=root/'stage'
            self.snapshot(folder/'current','one'); self.snapshot(folder/'previous','zero'); self.snapshot(stage,'two')
            (folder/'.transaction.json').write_text(json.dumps({'format':1,'stage':str(stage)}))
            (folder/'previous').rename(folder/'.old-previous')
            (folder/'current').rename(folder/'.old-current')
            stage.rename(folder/'current')
            a.recover(folder)
            self.assertEqual(a.verify(folder/'current')['commit_sha'],'two')
            self.assertEqual(a.verify(folder/'previous')['commit_sha'],'one')
            self.assertEqual(sorted(p.name for p in folder.iterdir()),['current','previous'])

if __name__=='__main__': unittest.main()
