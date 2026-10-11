"""Codex app-server token usage normalization, thread scoping and persistence."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from film.codex import Codex, normalize_token_usage
from film.core import Workspace


def sample_usage():
    return {
        'total': {
            'totalTokens': 1240,
            'inputTokens': 930,
            'cachedInputTokens': 120,
            'cacheWriteInputTokens': 30,
            'outputTokens': 310,
            'reasoningOutputTokens': 80,
        },
        'last': {
            'totalTokens': 340,
            'inputTokens': 260,
            'cachedInputTokens': 35,
            'cacheWriteInputTokens': 5,
            'outputTokens': 80,
            'reasoningOutputTokens': 20,
        },
        'modelContextWindow': 128000,
    }


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'workspace'
        self.ws = Workspace(self.path)
        self.codex = Codex(self.ws)

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def receive_usage(self, thread_id='thread-a', usage=None):
        self.codex.on_message({
            'method': 'thread/tokenUsage/updated',
            'params': {
                'threadId': thread_id,
                'turnId': 'turn-a',
                'tokenUsage': usage or sample_usage(),
            },
        })

    def test_normalization_keeps_known_counts_and_marks_missing_values_unavailable(self):
        raw = sample_usage()
        raw['total']['totalTokens'] = 0
        raw['total']['inputTokens'] = True
        raw['total']['cachedInputTokens'] = -1
        raw['total']['outputTokens'] = 2**54
        raw['modelContextWindow'] = 0

        usage = normalize_token_usage(raw)

        self.assertEqual(usage['total']['totalTokens'], 0)
        self.assertIsNone(usage['total']['inputTokens'])
        self.assertIsNone(usage['total']['cachedInputTokens'])
        self.assertIsNone(usage['total']['outputTokens'])
        self.assertIsNone(usage['modelContextWindow'])
        self.assertEqual(usage['last']['totalTokens'], 340)
        self.assertIsNone(normalize_token_usage({}))

    def test_usage_notification_is_scoped_to_the_active_thread(self):
        self.ws.data['thread_id'] = 'thread-a'
        self.ws.save()
        self.codex.thread_id = 'thread-a'

        self.receive_usage()
        accepted = self.codex.snapshot()

        self.assertEqual(accepted['token_usage'], normalize_token_usage(sample_usage()))
        self.assertEqual(accepted['events'][-1]['kind'], 'token_usage')
        self.assertEqual(accepted['events'][-1]['data'], accepted['token_usage'])
        event_count = len(accepted['events'])

        self.receive_usage(thread_id='old-thread')
        ignored = self.codex.snapshot()
        self.assertEqual(ignored['token_usage'], accepted['token_usage'])
        self.assertEqual(len(ignored['events']), event_count)

    def test_usage_survives_codex_reopen_only_for_the_saved_thread(self):
        self.ws.data['thread_id'] = 'thread-a'
        self.ws.save()
        self.codex.thread_id = 'thread-a'
        self.receive_usage()

        saved = json.loads((self.path / 'project.json').read_text())
        self.assertEqual(saved['codex_token_usage_thread_id'], 'thread-a')
        self.assertEqual(saved['codex_token_usage'], normalize_token_usage(sample_usage()))
        if os_name_is_posix():
            self.assertEqual(stat.S_IMODE((self.path / 'project.json').stat().st_mode) & 0o077, 0)

        reopened_ws = Workspace(self.path)
        try:
            reopened = Codex(reopened_ws)
            self.assertEqual(reopened.snapshot()['token_usage'], normalize_token_usage(sample_usage()))

            reopened_ws.data['thread_id'] = 'thread-b'
            reopened_ws.save()
            mismatched = Codex(reopened_ws)
            self.assertIsNone(mismatched.snapshot()['token_usage'])
        finally:
            reopened_ws.close()

    def test_new_chat_reset_clears_usage_and_preserves_permission_mode(self):
        self.ws.data['thread_id'] = 'thread-a'
        self.ws.data['codex_permissions'] = 'ask'
        self.ws.save()
        self.codex.thread_id = 'thread-a'
        self.receive_usage()

        self.assertTrue(self.codex.reset()['reset'])

        state = self.codex.snapshot()
        self.assertIsNone(state['token_usage'])
        self.assertEqual(self.ws.data['thread_id'], None)
        self.assertNotIn('codex_token_usage', self.ws.data)
        self.assertNotIn('codex_token_usage_thread_id', self.ws.data)
        self.assertEqual(self.ws.data['codex_permissions'], 'ask')
        self.assertEqual(json.loads((self.path / '.film/chat.json').read_text()), [])

    def test_activating_a_different_thread_discards_previous_usage(self):
        self.ws.data['thread_id'] = 'thread-a'
        self.ws.save()
        self.codex.thread_id = 'thread-a'
        self.receive_usage()

        self.codex._activate_thread('thread-b')

        self.assertIsNone(self.codex.snapshot()['token_usage'])
        self.assertEqual(self.ws.data['thread_id'], 'thread-b')
        self.assertNotIn('codex_token_usage', self.ws.data)


def os_name_is_posix():
    return os.name == 'posix'


if __name__ == '__main__':
    unittest.main()
