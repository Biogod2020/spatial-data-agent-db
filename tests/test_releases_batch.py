"""Release resolution, mirror-bound catalog loading, download plans and parallel downloads."""

from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from huggingface_hub import errors as hub_errors

from sda_db import (AccessError, Catalog, DEFAULT_REVISION, IntegrityError, NotFoundError, SizeLimitError,
                    download, plan_download, resolve_release)
from sda_db.batch import human_bytes, parse_size
from sda_db.errors import DiskFullError, NetworkError, translate
from sda_db.releases import known_releases

import fixtures

PUBLIC = "Biogod/SpatialDataAgent-DB-Catalog"


class ReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)

    def test_named_releases_resolve_offline(self) -> None:
        default = resolve_release(None, offline=True)
        self.assertEqual(default.revision, DEFAULT_REVISION)
        self.assertEqual(default.public_repo_id, PUBLIC)
        self.assertEqual(resolve_release("paper").name, default.name)
        self.assertEqual(resolve_release(default.revision).name, default.name)
        self.assertEqual(resolve_release("sdk-0.2").revision, "8f116b867ce4097957d4209db712d37bae3c9369")
        self.assertEqual(resolve_release("a" * 40).revision, "a" * 40)
        self.assertGreaterEqual(len(known_releases()), 2)

    def test_latest_needs_network_and_resolves_once(self) -> None:
        with self.assertRaisesRegex(NotFoundError, "Offline"):
            resolve_release("latest", offline=True)
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.repo_info.return_value.sha = "b" * 40
            release = resolve_release("latest")
        self.assertEqual(release.revision, "b" * 40)
        self.assertTrue(release.rolling)
        self.assertEqual(api.return_value.repo_info.call_args.kwargs["revision"], "main")


class OpenTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = fixtures.write_catalog_files(Path(folder.name))
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, repo: str, filename: str, **kwargs: object) -> str:
        self.calls.append((repo, filename, str(kwargs["revision"])))
        return str(self.paths[filename])

    def hashes(self) -> dict[str, str]:
        return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in self.paths.items()}

    def test_public_mirror_is_used_and_bound_by_hashes(self) -> None:
        with patch("huggingface_hub.hf_hub_download", side_effect=self.fetch), \
                patch("sda_db.catalog.resolve_release") as resolver:
            release = resolve_release(None)
            resolver.return_value = type(release)(**{**release.__dict__, "files": self.hashes()})
            catalog = Catalog.open()
        self.assertTrue(all(repo == PUBLIC for repo, _, _ in self.calls))
        self.assertEqual(catalog.repo_id, "Biogod/SpatialDataAgent-DB")  # downloads stay on the gated dataset
        self.assertEqual(catalog.revision, DEFAULT_REVISION)
        self.assertEqual(catalog.get("GSM1")["disease"], "Alzheimer's disease")
        self.assertEqual(catalog.get("GSM3")["study_title"], "Mouse kidney injury time course")
        self.assertIsNone(catalog.get("GSM5")["disease"])

    def test_mirror_bytes_that_differ_from_the_release_are_rejected(self) -> None:
        with patch("huggingface_hub.hf_hub_download", side_effect=self.fetch):
            with self.assertRaises(IntegrityError):
                Catalog.open()  # fixture CSV does not have the recorded release hash

    def test_gated_source_and_missing_optional_metadata(self) -> None:
        def fetch(repo: str, filename: str, **kwargs: object) -> str:
            if filename.endswith(".jsonl"):
                raise hub_errors.RemoteEntryNotFoundError("missing", response=httpx.Response(
                    404, request=httpx.Request("GET", "https://hub.invalid")))
            return self.fetch(repo, filename, **kwargs)

        with patch("huggingface_hub.hf_hub_download", side_effect=fetch):
            catalog = Catalog.open("c" * 40, source="gated")
        self.assertEqual({repo for repo, _, _ in self.calls}, {"Biogod/SpatialDataAgent-DB"})
        self.assertNotIn("study_title", catalog.get("GSM1"))
        self.assertEqual(len(catalog.search("kidney")), 1)  # sample fields still searchable

    def test_public_source_requires_a_mirror(self) -> None:
        with self.assertRaises(NotFoundError):
            Catalog.open("c" * 40, source="public")

    def test_gated_access_error_has_a_hint(self) -> None:
        response = httpx.Response(403, request=httpx.Request("GET", "https://hub.invalid"))
        with patch("huggingface_hub.hf_hub_download", side_effect=hub_errors.GatedRepoError("gated", response=response)):
            with self.assertRaises(AccessError) as caught:
                Catalog.open("c" * 40, source="gated")
        self.assertIn("Request access", caught.exception.hint)


class ErrorTranslationTests(unittest.TestCase):
    def test_translations(self) -> None:
        request = httpx.Request("GET", "https://hub.invalid")
        self.assertIsInstance(translate(hub_errors.HfHubHTTPError("x", response=httpx.Response(401, request=request))),
                              AccessError)
        self.assertIsInstance(translate(hub_errors.LocalEntryNotFoundError("x")), NetworkError)
        self.assertIsInstance(translate(httpx.ConnectError("x")), NetworkError)
        self.assertIsInstance(translate(OSError(errno.EDQUOT, "Disk quota exceeded")), DiskFullError)
        self.assertIsNone(translate(KeyError("x")))


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.payloads: dict[str, bytes] = {}
        rows = []
        for index in range(1, 5):
            gsm = f"GSM{index}"
            matrix, image = bytes([index]) * (1000 * index), bytes([index + 50]) * 300
            meta = {"schema": "sda-release-sample-1", "gsm": gsm, "gse": ["GSE1"], "published_gse": "GSE1",
                    "split": "train", "expression": {"file": "spatial.h5ad", "sha256": fixtures.digest(matrix),
                    "n_obs": 10, "n_vars": 2, "state": "counts"}, "coordinates": {"units": "pixel"},
                    "image": {"file": "image.tif", "sha256": fixtures.digest(image)}}
            prefix = f"samples/GSE1/{gsm}"
            self.payloads.update({f"{prefix}/sample.json": json.dumps(meta).encode(), f"{prefix}/spatial.h5ad": matrix,
                                  f"{prefix}/image.tif": image})
            rows.append(fixtures.row(gsm, "GSE1", n_obs=10, n_vars=2, has_image=True,
                                     expression_sha256=fixtures.digest(matrix), image_sha256=fixtures.digest(image)))
        manifest = "".join(json.dumps({"path": p, "bytes": len(b), "sha256": fixtures.digest(b)}) + "\n"
                           for p, b in self.payloads.items() if "GSM4" not in p)
        self.payloads["provenance/file-manifest.jsonl"] = manifest.encode()
        self.catalog = Catalog(rows)
        self.fail_for: set[str] = set()
        hub = patch("huggingface_hub.hf_hub_download", side_effect=self.fetch)
        hub.start()
        self.addCleanup(hub.stop)

    def fetch(self, repo: str, filename: str, **kwargs: object) -> str:
        if any(g in filename for g in self.fail_for):
            raise hub_errors.GatedRepoError("gated", response=httpx.Response(
                403, request=httpx.Request("GET", "https://hub.invalid")))
        base = Path(kwargs["local_dir"]) if kwargs.get("local_dir") else self.root / "hub"
        target = base / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payloads[filename])
        return str(target)

    def paths_info(self, repo: str, paths: list[str], **kwargs: object) -> list[object]:
        from types import SimpleNamespace

        return [SimpleNamespace(path=p, size=len(self.payloads[p])) for p in paths]

    def test_plan_uses_manifest_then_path_metadata_and_detects_local_files(self) -> None:
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.get_paths_info.side_effect = self.paths_info
            plan = plan_download(self.catalog, destination=self.root / "out", include_image=True)
        self.assertEqual(plan.total_bytes, sum(len(b) for p, b in self.payloads.items() if p.startswith("samples/")))
        self.assertEqual(plan.size_source, "release file manifest + Hub path metadata")
        self.assertEqual(plan.to_dict()["samples"], 4)
        download(self.catalog, self.root / "out", gsms=["GSM1"], progress=False)
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.get_paths_info.side_effect = self.paths_info
            again = plan_download(self.catalog, destination=self.root / "out")
        self.assertEqual(sorted(again.samples[0].local_files), ["sample.json", "spatial.h5ad"])
        self.assertLess(again.fetch_bytes, again.total_bytes)

    def test_size_limit_refuses_before_any_sample_transfer(self) -> None:
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.get_paths_info.side_effect = self.paths_info
            with self.assertRaises(SizeLimitError):
                download(self.catalog, self.root / "out", max_bytes="1KB", progress=False)
        self.assertFalse((self.root / "out").exists())

    def test_unknown_sizes_without_access_are_reported_not_guessed(self) -> None:
        self.fail_for = {"provenance"}
        plan = plan_download(self.catalog, destination=self.root / "out")
        self.assertIsNone(plan.total_bytes)
        self.assertEqual(plan.size_source, "unavailable")
        self.assertTrue(any("access" in n for n in plan.notes))
        self.assertTrue(plan.access_denied)
        with self.assertRaises(AccessError):  # fail closed, with the access hint rather than a size guess
            download(self.catalog, self.root / "out", max_bytes="1GB", progress=False)

    def test_interrupt_cancels_queued_samples(self) -> None:
        from threading import Event as ThreadEvent

        from sda_db import batch

        started, release = [], ThreadEvent()

        def slow(catalog, gsm, *args, _cancel=None, **kwargs):
            started.append(gsm)
            if gsm == "GSM1":
                raise KeyboardInterrupt
            release.wait(2)
            return self.root / gsm

        with patch.object(batch, "download_sample", side_effect=slow):
            with self.assertRaises(KeyboardInterrupt):
                download(self.catalog, self.root / "out", workers=1, progress=False)
        self.assertEqual(started, ["GSM1"])  # nothing queued ran after the interrupt

    def test_parallel_download_keeps_order_and_continues_after_failures(self) -> None:
        self.fail_for = {"GSM3/"}
        report = download(self.catalog, self.root / "out", workers=3, on_error="continue", progress=False)
        self.assertEqual([c["gsm"] for c in report.completed], ["GSM1", "GSM2", "GSM4"])
        self.assertEqual(report.failed[0]["gsm"], "GSM3")
        self.assertEqual(report.failed[0]["error"]["code"], "access_denied")
        self.assertFalse(report.ok)
        saved = json.loads(report.save(self.root / "report.json").read_text())
        self.assertIn("1 failed", saved["summary"])

    def test_raise_mode_reraises_the_first_error_with_report(self) -> None:
        self.fail_for = {"GSM1/"}
        with self.assertRaises(AccessError) as caught:
            download(self.catalog, self.root / "out", workers=1, progress=False)
        self.assertEqual(caught.exception.report.failed[0]["gsm"], "GSM1")

    def test_size_parsing(self) -> None:
        self.assertEqual(parse_size("1.5GB"), 1_500_000_000)
        self.assertEqual(parse_size("800M"), 800_000_000)
        self.assertEqual(parse_size("2GiB"), 2 * 1024**3)
        with self.assertRaises(ValueError):
            parse_size("1e400")
        self.assertEqual(parse_size(42), 42)
        self.assertEqual(human_bytes(1_234_000), "1.2 MB")
        with self.assertRaises(ValueError):
            parse_size("lots")


if __name__ == "__main__":
    unittest.main()
