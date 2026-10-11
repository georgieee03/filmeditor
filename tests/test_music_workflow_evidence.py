"""Regressions for the rich-result shapes observed in native Codex readback."""
import json
import unittest

from native_music_workflow import clean, result_objects


class MusicEvidenceTests(unittest.TestCase):
    def test_serialized_rich_result_is_redacted_before_truncation(self):
        encoded = "aGVsbG8=" * 5000
        inner = {"content": [
            {"type": "text", "text": "/workspace/cache/source-frame.png"},
            {"type": "image", "mimeType": "image/png", "data": encoded},
            {"type": "audio", "mimeType": "audio/wav", "data": encoded},
        ], "access_token": "private-token", "reasoning": "private-reasoning"}
        stored = {"content": [{"type": "text", "text": json.dumps(inner)}]}
        sanitized = clean(stored)
        payload = json.dumps(sanitized)
        self.assertNotIn(encoded[:100], payload)
        self.assertNotIn("private-token", payload)
        self.assertNotIn("private-reasoning", payload)
        recovered = json.loads(sanitized["content"][0]["text"])
        self.assertEqual(recovered["content"][0]["text"], "/workspace/cache/source-frame.png")
        self.assertEqual(recovered["content"][1], {"type": "image", "mimeType": "image/png"})
        # Storage nesting must remain observable; it is not typed-delivery proof.
        images = [(block, depth) for result, depth in result_objects(sanitized)
                  for block in result.get("content", []) if block.get("type") == "image"]
        self.assertEqual(len(images), 1)
        self.assertGreater(images[0][1], 0)

    def test_older_truncated_image_payload_is_not_retained(self):
        fragment = '{"content":[{"type":"image","data":"' + "aGVsbG8=" * 2000 + " [truncated]"
        sanitized = clean(fragment)
        self.assertNotIn("aGVsbG8=" * 10, sanitized)
        self.assertIn("media data omitted", sanitized)

    def test_nested_tool_failure_remains_a_failure_after_sanitizing(self):
        error = {"isError": True, "content": [{"type": "text", "text": "progressive must be an integer"}]}
        stored = {"content": [{"type": "text", "text": json.dumps(error)}]}
        recovered = list(result_objects(clean(stored)))
        self.assertTrue(any(result.get("isError") for result, _ in recovered))
        self.assertIn("progressive must be an integer", json.dumps(recovered))


if __name__ == "__main__":
    unittest.main()
