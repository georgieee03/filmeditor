"""Import cancellation preserves committed source copies; no engine is invoked."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from film.core import Workspace


class ImportCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ws=Workspace(Path(self.tmp.name)/'workspace')
        self.committed=self.ws.ingest(io.BytesIO(b'earlier import'),14,'earlier.mp4','footage')
        self.original=Path(self.committed['path']).read_bytes()
        self.digest=hashlib.sha256(self.original).hexdigest()

    def tearDown(self):
        self.ws.close();self.tmp.cleanup()

    def assert_prior_import_retained(self):
        self.assertEqual(self.ws.data['assets'],[self.committed])
        self.assertEqual(Path(self.committed['path']).read_bytes(),self.original)
        self.assertEqual(hashlib.sha256(Path(self.committed['path']).read_bytes()).hexdigest(),self.digest)
        self.assertEqual(list((self.ws.root/'originals').iterdir()),[Path(self.committed['path'])])

    def test_import_refuses_an_already_stopping_workspace(self):
        self.ws.stopping=True
        with self.assertRaisesRegex(ValueError,'import cancelled'):
            self.ws.ingest(io.BytesIO(b'new source'),10,'new.mp4','footage')
        self.assert_prior_import_retained()

    def test_shutdown_during_first_or_last_chunk_discards_only_current_import(self):
        for length in (17,2*1024**2):
            with self.subTest(length=length):
                self.ws.stopping=False
                ws=self.ws
                class StopOnRead(io.BytesIO):
                    def read(self,size):
                        data=super().read(size)
                        ws.stopping=True
                        return data
                with self.assertRaisesRegex(ValueError,'import cancelled'):
                    self.ws.ingest(StopOnRead(b'x'*length),length,'cancelled.mp4','footage')
                self.assert_prior_import_retained()

    def test_shutdown_after_copy_before_publication_leaves_no_source_or_upload(self):
        def stop_after_flush(_):self.ws.stopping=True
        with patch('film.core.os.fsync',side_effect=stop_after_flush):
            with self.assertRaisesRegex(ValueError,'import cancelled'):
                self.ws.ingest(io.BytesIO(b'complete copy'),13,'cancelled.mp4','footage')
        self.assert_prior_import_retained()


if __name__=='__main__':unittest.main()
