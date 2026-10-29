"""Stream scheduling, eviction and integrity checks with synthetic offline transfers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, Callable
import unittest
from unittest.mock import patch

from filelock import Timeout

from sda_db import CacheLimitError, Catalog, DEFAULT_REVISION, download_sample, stream_samples, verify_sample


class StreamTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.payloads: dict[str, bytes] = {}
        rows = []
        for index in range(1, 6):
            gsm = f"GSM{index}"
            matrix = bytes([index]) * 2048
            image = bytes([index + 10]) * 512
            expression_hash = hashlib.sha256(matrix).hexdigest()
            image_hash = hashlib.sha256(image).hexdigest()
            meta = {"schema": "sda-release-sample-1", "gsm": gsm, "gse": ["GSE1"], "published_gse": "GSE1",
                    "split": "train", "expression": {"file": "spatial.h5ad", "sha256": expression_hash,
                    "n_obs": index, "n_vars": 2, "state": "counts"}, "coordinates": {"units": "pixel"},
                    "image": {"file": "image.tif", "sha256": image_hash}}
            prefix = f"samples/GSE1/{gsm}"
            self.payloads[f"{prefix}/sample.json"] = json.dumps(meta).encode()
            self.payloads[f"{prefix}/spatial.h5ad"] = matrix
            self.payloads[f"{prefix}/image.tif"] = image
            rows.append({"gsm": gsm, "gse": "GSE1", "published_gse": "GSE1", "sample_dir": prefix,
                         "n_obs": index, "n_vars": 2, "has_image": True, "expression_sha256": expression_hash,
                         "image_sha256": image_hash, "expression_state": "counts", "coordinate_units": "pixel",
                         "split": "train"})
        self.catalog = Catalog(rows)
        self.sample_size = sum(len(self.payloads[f"samples/GSE1/GSM1/{name}"])
                               for name in ("sample.json", "spatial.h5ad"))
        self.before_download: Callable[[str], None] = lambda filename: None
        self.after_head: Callable[[str], None] = lambda filename: None
        self.transfers: list[str] = []
        self.heads: list[str] = []
        hub = patch("huggingface_hub.hf_hub_download", side_effect=self.fetch)
        self.mock_download = hub.start()
        self.addCleanup(hub.stop)
        metadata = patch("huggingface_hub.get_hf_file_metadata", side_effect=self.head)
        self.mock_head = metadata.start()
        self.addCleanup(metadata.stop)

    def fetch(self, repo: str, filename: str, **options: Any) -> str:
        self.assertEqual(options["revision"], DEFAULT_REVISION)
        self.assertEqual(options["repo_type"], "dataset")
        self.assertIn("local_dir", options)
        self.before_download(filename)
        path = Path(options["local_dir"]) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payloads[filename])
        self.transfers.append(filename)
        return str(path)

    def head(self, url: str, **kwargs: Any) -> Any:
        filename = "samples/" + url.split("/samples/", 1)[1]
        self.heads.append(filename)
        result = SimpleNamespace(size=len(self.payloads[filename]), commit_hash=DEFAULT_REVISION)
        self.after_head(filename)
        return result

    def stream(self, gsms: list[str], **kwargs: Any) -> Any:
        options = {"cache_dir": self.cache, "max_cache_bytes": self.sample_size * 3, "prefetch": 1}
        options.update(kwargs)
        return stream_samples(self.catalog, gsms, **options)

    def test_validated_ids_and_context_required_before_any_work(self) -> None:
        with self.assertRaisesRegex(ValueError, "absent"):
            self.stream(["GSM1", "GSM99"])
        with self.assertRaisesRegex(RuntimeError, "with"):
            next(self.stream(["GSM1"]))
        self.assertFalse(self.cache.exists())
        self.mock_download.assert_not_called()

    def test_order_duplicates_and_completed_receipts(self) -> None:
        with self.stream(["GSM2", "GSM1", "GSM2"], prefetch=0) as samples:
            seen = []
            for sample in samples:
                seen.append(sample.gsm)
                self.assertTrue(verify_sample(sample.directory)["receipt_verified"])
            self.assertEqual(seen, ["GSM2", "GSM1"])
            self.assertEqual(samples.cache_info["pinned_samples"], 0)

    def test_prefetch_runs_while_first_sample_is_in_use_and_is_bounded(self) -> None:
        second_started, finish_second = Event(), Event()

        def pause(filename: str) -> None:
            if filename == "samples/GSE1/GSM2/spatial.h5ad":
                second_started.set()
                if not finish_second.wait(4):
                    raise AssertionError("Test did not release second transfer")

        self.before_download = pause
        try:
            with self.stream(["GSM1", "GSM2", "GSM3", "GSM4"]) as samples:
                first = next(samples)
                self.assertTrue(second_started.wait(3), "Second sample did not prefetch during consumption")
                self.assertTrue((first.directory / "spatial.h5ad").is_file())
                self.assertFalse(any("GSM3/" in filename for filename in self.heads))
                finish_second.set()
                self.assertEqual(next(samples).gsm, "GSM2")
        finally:
            finish_second.set()

    def test_budget_backpressure_keeps_active_sample_and_evicts_after_advance(self) -> None:
        second_planned = Event()
        self.after_head = lambda name: second_planned.set() if name == "samples/GSE1/GSM2/spatial.h5ad" else None
        with self.stream(["GSM1", "GSM2"], max_cache_bytes=self.sample_size) as samples:
            first = next(samples)
            first_path = first.directory
            self.assertTrue(second_planned.wait(3))
            self.assertFalse(any("GSM2/" in name for name in self.transfers))
            self.assertTrue(first_path.exists())
            second = next(samples)
            self.assertEqual(second.gsm, "GSM2")
            self.assertFalse(first_path.exists())
            self.assertLessEqual(samples.cache_info["allocated_bytes"], self.sample_size)
            with self.assertRaisesRegex(RuntimeError, "released"):
                first.directory

    def test_oversized_sample_rejected_before_payload_download(self) -> None:
        with self.stream(["GSM1"], max_cache_bytes=self.sample_size - 1) as samples:
            with self.assertRaisesRegex(CacheLimitError, "GSM1 needs"):
                next(samples)
        self.mock_download.assert_not_called()

    def test_image_is_opt_in_and_reserved_before_download(self) -> None:
        with self.stream(["GSM1"], max_cache_bytes=self.sample_size, include_image=True) as samples:
            with self.assertRaises(CacheLimitError):
                next(samples)
        self.mock_download.assert_not_called()
        with self.stream(["GSM1"], include_image=True) as samples:
            first = next(samples)
            self.assertTrue(verify_sample(first.directory, require_image=True)["receipt_verified"])

    def test_cached_sample_reopens_offline_without_head_or_download(self) -> None:
        with self.stream(["GSM1"]) as samples:
            path = next(samples).directory
        self.mock_download.reset_mock()
        self.mock_head.reset_mock()
        offline = Catalog(self.catalog.rows, local_files_only=True)
        with stream_samples(offline, ["GSM1"], cache_dir=self.cache) as samples:
            self.assertEqual(next(samples).directory, path)
        self.mock_download.assert_not_called()
        self.mock_head.assert_not_called()

    def test_offline_missing_sample_never_attempts_network(self) -> None:
        offline = Catalog(self.catalog.rows, local_files_only=True)
        with stream_samples(offline, ["GSM1"], cache_dir=self.cache) as samples:
            with self.assertRaisesRegex(FileNotFoundError, "offline"):
                next(samples)
        self.mock_download.assert_not_called()
        self.mock_head.assert_not_called()

    def test_lru_preserves_recently_reused_sample(self) -> None:
        paths = {}
        with self.stream(["GSM1", "GSM2"], max_cache_bytes=self.sample_size * 2, prefetch=0) as samples:
            for sample in samples:
                paths[sample.gsm] = sample.directory
        with self.stream(["GSM1"], max_cache_bytes=self.sample_size * 2) as samples:
            next(samples)
        with self.stream(["GSM3"], max_cache_bytes=self.sample_size * 2) as samples:
            next(samples)
        self.assertTrue(paths["GSM1"].exists())
        self.assertFalse(paths["GSM2"].exists())

    def test_exclusive_cache_owner_prevents_cross_stream_eviction(self) -> None:
        with self.stream(["GSM1"]) as samples:
            first = next(samples)
            with self.assertRaises(Timeout):
                with self.stream(["GSM2"]):
                    pass
            self.assertTrue(first.directory.exists())

    def test_unmanaged_directory_is_preserved(self) -> None:
        self.cache.mkdir()
        original = self.cache / "user-data.txt"
        original.write_text("keep")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            with self.stream(["GSM1"]):
                pass
        self.assertEqual(original.read_text(), "keep")
        self.assertEqual(list(self.cache.iterdir()), [original])

    def test_corrupt_transfer_never_yielded_and_cache_lock_is_released(self) -> None:
        self.payloads["samples/GSE1/GSM2/spatial.h5ad"] = b"bad" * 683  # Different digest and length.
        with self.stream(["GSM1", "GSM2"], prefetch=0) as samples:
            first = next(samples)
            self.assertEqual(first.gsm, "GSM1")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                next(samples)
        self.assertEqual(len(list((self.cache / "entries").iterdir())), 1)
        with self.stream(["GSM1"]) as samples:
            self.assertEqual(next(samples).gsm, "GSM1")

    def test_early_close_releases_waiting_prefetch_and_does_not_start_later_samples(self) -> None:
        second_planned = Event()
        self.after_head = lambda name: second_planned.set() if "GSM2/spatial.h5ad" in name else None
        with self.stream(["GSM1", "GSM2", "GSM3"], max_cache_bytes=self.sample_size) as samples:
            first = next(samples)
            self.assertTrue(second_planned.wait(3))
        self.assertEqual(samples.cache_info["pinned_samples"], 0)
        self.assertFalse(any("GSM2/" in name or "GSM3/" in name for name in self.transfers))
        with self.assertRaises(RuntimeError):
            first.directory

    def test_close_waits_for_active_file_then_cleans_unfinished_entry(self) -> None:
        started, finish = Event(), Event()

        def pause(name: str) -> None:
            if name == "samples/GSE1/GSM2/spatial.h5ad":
                started.set()
                if not finish.wait(4):
                    raise AssertionError("Cancellation test did not release its file transfer")

        self.before_download = pause
        with self.stream(["GSM1", "GSM2", "GSM3"]) as samples:
            next(samples)
            self.assertTrue(started.wait(3))

            def release_on_close() -> None:
                samples._stop.wait(3)
                finish.set()

            helper = Thread(target=release_on_close)
            helper.start()
        helper.join(timeout=3)
        self.assertFalse(helper.is_alive())
        self.assertFalse(any("GSM3/" in name for name in self.transfers))
        self.assertEqual(samples.cache_info["reserved_bytes"], 0)
        self.assertEqual(len(list((self.cache / "entries").iterdir())), 1)

    def test_explicit_release_allows_prefetch_to_reclaim_sample(self) -> None:
        with self.stream(["GSM1", "GSM2"], max_cache_bytes=self.sample_size) as samples:
            first = next(samples)
            first.release()
            with self.assertRaises(RuntimeError):
                first.directory
            self.assertEqual(next(samples).gsm, "GSM2")

    def test_reopening_with_smaller_budget_prunes_existing_cache(self) -> None:
        with self.stream(["GSM1", "GSM2", "GSM3"], prefetch=0) as samples:
            for sample in samples:
                sample.directory
        with self.stream([], max_cache_bytes=self.sample_size) as samples:
            self.assertLessEqual(samples.cache_info["allocated_bytes"], self.sample_size)
            self.assertEqual(samples.cache_info["cached_samples"], 1)

    def test_interrupted_managed_entry_is_removed_on_reopen(self) -> None:
        with self.stream(["GSM1"]) as samples:
            next(samples)
        record_path = next((self.cache / "entries").glob("*/entry.json"))
        record = json.loads(record_path.read_text())
        record["state"] = "partial"
        record_path.write_text(json.dumps(record))
        with self.stream([]) as samples:
            self.assertEqual(samples.cache_info["allocated_bytes"], 0)
        self.assertFalse(record_path.exists())

    def test_cache_symlink_escape_rejected(self) -> None:
        with self.stream([]):
            pass
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "keep"
        sentinel.write_text("unchanged")
        (self.cache / "entries").rmdir()
        (self.cache / "entries").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            with self.stream([]):
                pass
        self.assertEqual(sentinel.read_text(), "unchanged")

    def test_direct_download_has_no_sdk_copy_and_leaves_one_payload(self) -> None:
        with patch("sda_db.download.shutil.copyfileobj", side_effect=AssertionError("No full-size SDK copy")):
            directory = download_sample(self.catalog, "GSM1", self.root / "direct", storage="direct")
        self.assertTrue(verify_sample(directory)["receipt_verified"])
        self.assertEqual(len(list((self.root / "direct").rglob("spatial.h5ad"))), 1)
        self.mock_download.reset_mock()
        self.assertEqual(download_sample(self.catalog, "GSM1", self.root / "direct", storage="direct"), directory)
        self.mock_download.assert_not_called()

    def test_prefetch_zero_avoids_background_work(self) -> None:
        with self.stream(["GSM1", "GSM2"], prefetch=0) as samples:
            next(samples)
            self.assertFalse(any("GSM2/" in name for name in self.heads + self.transfers))

    def test_invalid_head_commit_stops_before_payload_transfer(self) -> None:
        self.mock_head.side_effect = None
        self.mock_head.return_value = SimpleNamespace(size=100, commit_hash="0" * 40)
        with self.stream(["GSM1"]) as samples:
            with self.assertRaisesRegex(ValueError, "invalid size or commit"):
                next(samples)
        self.mock_download.assert_not_called()

    def test_preflight_size_disagreement_is_rejected_and_cleaned(self) -> None:
        def incorrect_size(url: str, **options: Any) -> Any:
            result = self.head(url, **options)
            if url.endswith("spatial.h5ad"):
                result.size -= 1
            return result

        self.mock_head.side_effect = incorrect_size
        with self.stream(["GSM1"]) as samples:
            with self.assertRaisesRegex(ValueError, "sizes changed"):
                next(samples)
        self.assertEqual(list((self.cache / "entries").iterdir()), [])

    def test_direct_corrupt_download_is_not_installed(self) -> None:
        self.payloads["samples/GSE1/GSM1/spatial.h5ad"] = b"bad bytes"
        with self.assertRaisesRegex(ValueError, "SHA256"):
            download_sample(self.catalog, "GSM1", self.root / "direct", storage="direct")
        final = self.root / "direct" / DEFAULT_REVISION / "samples/GSE1/GSM1/spatial.h5ad"
        self.assertFalse(final.exists())

    def test_missing_images_can_be_added_to_cached_expression(self) -> None:
        with self.stream(["GSM1"]) as samples:
            directory = next(samples).directory
            self.assertFalse((directory / "image.tif").exists())
        old_transfers = len(self.transfers)
        with self.stream(["GSM1"], include_image=True) as samples:
            upgraded = next(samples).directory
            self.assertEqual(upgraded, directory)
            self.assertTrue((upgraded / "image.tif").is_file())
        self.assertEqual(self.transfers[old_transfers:], ["samples/GSE1/GSM1/image.tif"])


if __name__ == "__main__":
    unittest.main()
