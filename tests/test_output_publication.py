"""Output-list publication regressions; bytes are fixtures, not rendered media."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from film.core import Workspace


class OutputPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name) / 'workspace')

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def output(self, relative, mtime=100):
        path = self.ws.root / relative
        path.write_bytes(b'output fixture')
        os.utime(path, (mtime, mtime))
        return path

    def test_hidden_render_is_listed_only_after_atomic_publication(self):
        self.output('exports/previous.mp4', 100)
        staging = self.output('exports/.film-reel.0123456789abcdef.tmp.mp4', 200)
        self.assertEqual(self.ws.state()['outputs'], ['exports/previous.mp4'])

        # The engine continues writing under its private staging name.
        with staging.open('ab') as stream:
            stream.write(b' more frames')
        self.assertEqual(self.ws.state()['outputs'], ['exports/previous.mp4'])

        final = self.ws.root / 'exports/film-reel.mp4'
        os.replace(staging, final)
        self.assertEqual(self.ws.state()['outputs'],
                         ['exports/film-reel.mp4', 'exports/previous.mp4'])
        self.assertEqual(final.read_bytes(), b'output fixture more frames')

    def test_completed_outputs_and_credits_keep_directory_and_mtime_order(self):
        expected = ['exports/reel.mp4.credits.txt', 'exports/reel.MOV',
                    'exports/older.mp4', 'generated/scene.mov',
                    'cache/new-frame.png', 'cache/older-frame.jpg']
        for relative, mtime in zip(expected, [300, 200, 100, 600, 500, 400]):
            self.output(relative, mtime)
        for relative in ['exports/reel.mp4.deadbeef.tmp', 'exports/render.log',
                         'generated/scene.mov.credits.txt', 'cache/probe.json',
                         'cache/.frame.png', 'generated/.scene.tmp.mov',
                         'exports/.reel.mp4.credits.txt']:
            self.output(relative, 1000)
        self.assertEqual(self.ws.state()['outputs'], expected)

    def test_media_named_directories_and_broken_links_are_not_outputs(self):
        self.output('exports/completed.mp4')
        for relative in ['exports/folder.mp4', 'exports/folder.credits.txt',
                         'generated/folder.mov', 'cache/folder.png']:
            (self.ws.root / relative).mkdir()
        (self.ws.root / 'exports/missing.mov').symlink_to('not-present.mov')
        self.assertEqual(self.ws.state()['outputs'], ['exports/completed.mp4'])

    def test_output_vanishing_before_stat_does_not_break_state(self):
        vanished = self.output('exports/vanishing.mp4')
        self.output('exports/completed.mp4')
        self.output('cache/frame.png')
        original_stat = Path.stat
        removed = []

        def stat_after_removal(path, *args, **kwargs):
            if path == vanished and not removed:
                path.unlink()
                removed.append(path)
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(Path, 'stat', stat_after_removal):
            self.assertEqual(self.ws.state()['outputs'],
                             ['exports/completed.mp4', 'cache/frame.png'])
        self.assertEqual(removed, [vanished])


if __name__ == '__main__':
    unittest.main()
