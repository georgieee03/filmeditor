"""Delivery publication regressions; source bytes are fixtures, not renders."""
from pathlib import Path
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from film import delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = self.base / 'library'
        self.library.mkdir()
        self.workspace_root = self.base / 'workspace'
        (self.workspace_root / 'exports').mkdir(parents=True)
        self.ws = types.SimpleNamespace(root=self.workspace_root)
        delivery.bind_workspace(self.ws, self.library, 'project-1', 'Summer Reel')

    def tearDown(self):
        self.tmp.cleanup()

    def output(self, relative='exports/final.mp4', content=b'fixture video bytes'):
        path = self.workspace_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def job(self, path, *, status='completed', job_id='render-1'):
        return {'job_id': job_id, 'status': status, 'output_path': str(path)}

    def test_unbound_workspace_reports_delivery_unavailable(self):
        result = delivery.export_info(types.SimpleNamespace(root=self.workspace_root))
        self.assertEqual(result, {
            'available': False,
            'delivery_folder': None,
            'videos': [],
        })

    def test_only_completed_jobs_are_published(self):
        source = self.output()
        for status in ('queued', 'running', 'failed', 'succeeded', None):
            job = self.job(source, status=status, job_id=f'job-{status}')
            result = delivery.publish_completed(self.ws, job)
            self.assertEqual(result, job)
            self.assertNotIn('delivery_warning', result)
        info = delivery.export_info(self.ws)
        self.assertTrue(info['available'])
        self.assertEqual(info['videos'], [])
        self.assertEqual(list(Path(info['delivery_folder']).iterdir()), [])

    def test_completed_output_is_copied_and_render_success_is_preserved(self):
        source = self.output(content=b'known fixture bytes')
        original = self.job(source)
        result = delivery.publish_completed(self.ws, original)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(original, self.job(source))
        self.assertNotIn('delivery_warning', result)
        self.assertEqual(Path(result['delivery_path']).read_bytes(), b'known fixture bytes')
        self.assertEqual(result['delivery_folder'], str(Path(result['delivery_path']).parent))

    def test_only_regular_video_files_in_exports_can_be_delivered(self):
        outside = self.base / 'outside.mp4'
        outside.write_bytes(b'outside')
        cache = self.output('cache/frame.mp4')
        generated = self.output('generated/scene.mov')
        audio = self.output('exports/voice.wav')
        nested = self.output('exports/nested/allowed.webm')
        directory = self.workspace_root / 'exports/folder.mov'
        directory.mkdir()
        symlink = self.workspace_root / 'exports/link.mp4'
        symlink.symlink_to(nested)

        invalid_jobs = [
            self.job(outside, job_id='outside'),
            self.job(cache, job_id='cache'),
            self.job(generated, job_id='generated'),
            self.job(audio, job_id='audio'),
            self.job(directory, job_id='directory'),
            self.job(symlink, job_id='symlink'),
        ]
        for job in invalid_jobs:
            result = delivery.publish_completed(self.ws, job)
            self.assertEqual(result['status'], 'completed')
            self.assertIn('delivery_warning', result)
            self.assertNotIn('delivery_path', result)

        result = delivery.publish_completed(self.ws, self.job(nested, job_id='nested'))
        self.assertNotIn('delivery_warning', result)
        self.assertEqual(Path(result['delivery_path']).read_bytes(), nested.read_bytes())
        self.assertEqual([item['job_id'] for item in delivery.export_info(self.ws)['videos']], ['nested'])

    def test_public_folder_name_is_safe_and_unowned_collisions_are_preserved(self):
        preexisting = self.library / 'Summer Reel'
        preexisting.mkdir()
        (preexisting / 'keep.txt').write_text('unowned')
        source = self.output()
        result = delivery.publish_completed(self.ws, self.job(source))

        folder = Path(result['delivery_folder'])
        self.assertNotEqual(folder, preexisting)
        self.assertTrue(folder.name.startswith('Summer Reel-'))
        self.assertEqual((preexisting / 'keep.txt').read_text(), 'unowned')

        other_root = self.base / 'other-workspace'
        (other_root / 'exports').mkdir(parents=True)
        other = types.SimpleNamespace(root=other_root)
        delivery.bind_workspace(other, self.library, 'project-2', '../exports')
        other_info = delivery.export_info(other)
        other_folder = Path(other_info['delivery_folder'])
        self.assertEqual(other_folder.parent.resolve(), self.library.resolve())
        self.assertNotEqual(other_folder.name.casefold(), 'exports')
        self.assertNotIn('/', other_folder.name)
        self.assertNotIn('..', other_folder.name)

    def test_idempotent_across_calls_and_workspace_reopen(self):
        source = self.output()
        job = self.job(source)
        first = delivery.publish_completed(self.ws, job)
        first_path = Path(first['delivery_path'])
        first_identity = (first_path.stat().st_dev, first_path.stat().st_ino)

        second = delivery.publish_completed(self.ws, job)
        reopened = types.SimpleNamespace(root=self.workspace_root)
        delivery.bind_workspace(reopened, self.library, 'project-1', 'Summer Reel')
        third = delivery.publish_completed(reopened, job)

        self.assertEqual(second['delivery_path'], first['delivery_path'])
        self.assertEqual(third['delivery_path'], first['delivery_path'])
        self.assertEqual((first_path.stat().st_dev, first_path.stat().st_ino), first_identity)
        self.assertEqual(len(list(first_path.parent.iterdir())), 1)
        self.assertEqual(len(delivery.export_info(reopened)['videos']), 1)

    def test_linked_video_is_recovered_if_final_manifest_write_fails(self):
        source = self.output()
        delivery.export_info(self.ws)  # Persist the empty manifest before injection.
        original_write = delivery._write_metadata

        def fail_published_state(directory_fd, name, value):
            saved = value.get('jobs', {}).get('render-1', {}) if name == 'deliveries.json' else {}
            if saved.get('state') == 'published':
                raise delivery.DeliveryError('simulated final metadata interruption')
            return original_write(directory_fd, name, value)

        with mock.patch.object(delivery, '_write_metadata', side_effect=fail_published_state):
            interrupted = delivery.publish_completed(self.ws, self.job(source))
        self.assertIn('delivery_warning', interrupted)

        public_folder = Path(delivery.export_info(self.ws)['delivery_folder'])
        public_video = public_folder / delivery._delivery_filename(source.name, 'render-1')
        stage_directory = self.library / '.film' / 'delivery-staging'
        self.assertTrue(public_video.is_file())
        linked_identity = (public_video.stat().st_dev, public_video.stat().st_ino)
        self.assertEqual(len(list(stage_directory.iterdir())), 1)

        recovered = delivery.publish_completed(self.ws, self.job(source))
        self.assertNotIn('delivery_warning', recovered)
        recovered_path = Path(recovered['delivery_path'])
        self.assertTrue(recovered_path.is_file())
        self.assertEqual(recovered_path, public_video)
        self.assertEqual((recovered_path.stat().st_dev, recovered_path.stat().st_ino), linked_identity)
        self.assertEqual(list(stage_directory.iterdir()), [])
        self.assertEqual(len(delivery.export_info(self.ws)['videos']), 1)

    def test_failed_pending_manifest_write_cleans_unowned_stage(self):
        source = self.output()
        delivery.export_info(self.ws)
        original_write = delivery._write_metadata

        def fail_pending_state(directory_fd, name, value):
            saved = value.get('jobs', {}).get('render-1', {}) if name == 'deliveries.json' else {}
            if saved.get('state') == 'publishing':
                raise delivery.DeliveryError('simulated pending metadata failure')
            return original_write(directory_fd, name, value)

        with mock.patch.object(delivery, '_write_metadata', side_effect=fail_pending_state):
            interrupted = delivery.publish_completed(self.ws, self.job(source))
        self.assertIn('delivery_warning', interrupted)

        stage_directory = self.library / '.film' / 'delivery-staging'
        self.assertEqual(list(stage_directory.iterdir()), [])
        manifest = json.loads((self.workspace_root / '.film' / 'deliveries.json').read_text())
        self.assertNotIn('render-1', manifest['jobs'])
        public_folder = Path(delivery.export_info(self.ws)['delivery_folder'])
        self.assertEqual(list(public_folder.iterdir()), [])

        retried = delivery.publish_completed(self.ws, self.job(source))
        self.assertNotIn('delivery_warning', retried)
        self.assertTrue(Path(retried['delivery_path']).is_file())
        self.assertEqual(list(stage_directory.iterdir()), [])

    def test_export_info_lists_only_manifest_verified_deliveries(self):
        source = self.output()
        result = delivery.publish_completed(self.ws, self.job(source))
        untracked = Path(result['delivery_folder']) / 'untracked.mp4'
        untracked.write_bytes(b'not published by this workspace')

        info = delivery.export_info(self.ws)
        self.assertTrue(info['available'])
        self.assertEqual(len(info['videos']), 1)
        self.assertEqual(info['videos'][0]['name'], Path(result['delivery_path']).name)

    def test_replaced_delivery_folder_is_rejected(self):
        source = self.output()
        first = delivery.publish_completed(self.ws, self.job(source))
        folder = Path(first['delivery_folder'])
        moved = self.library / 'old-owned-folder'
        folder.rename(moved)
        replacement = self.base / 'replacement'
        replacement.mkdir()
        folder.symlink_to(replacement, target_is_directory=True)

        second = delivery.publish_completed(
            self.ws,
            self.job(source, job_id='render-2'),
        )
        self.assertIn('delivery_warning', second)
        self.assertNotIn('delivery_path', second)
        self.assertEqual(list(replacement.iterdir()), [])
        self.assertEqual(len(list(moved.iterdir())), 1)

    def test_existing_delivery_filename_is_never_overwritten(self):
        source = self.output()
        filename = delivery._delivery_filename(source.name, 'render-1')
        folder = Path(delivery.export_info(self.ws)['delivery_folder'])
        occupied = folder / filename
        occupied.write_bytes(b'preexisting')

        result = delivery.publish_completed(self.ws, self.job(source))
        self.assertIn('delivery_warning', result)
        self.assertEqual(occupied.read_bytes(), b'preexisting')

    def test_private_metadata_uses_restricted_permissions(self):
        source = self.output()
        result = delivery.publish_completed(self.ws, self.job(source))
        self.assertNotIn('delivery_warning', result)
        workspace_private = self.workspace_root / '.film'
        library_private = self.library / '.film'
        self.assertEqual(workspace_private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(library_private.stat().st_mode & 0o777, 0o700)
        for path in [workspace_private / 'deliveries.json', library_private / 'delivery-folders.json']:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
