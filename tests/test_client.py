"""Offline behavior tests with synthetic byte payloads; never contact the database."""

from __future__ import annotations

from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from sda_db import Catalog, DEFAULT_REVISION, download_sample, verify_sample
from sda_db.cli import main


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Tests must be offline"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "spatial.h5ad").write_bytes(b"synthetic expression bytes, not a scientific sample")
        (self.source / "image.tif").write_bytes(b"synthetic image bytes")
        self.expression_hash = self.digest(self.source / "spatial.h5ad")
        self.image_hash = self.digest(self.source / "image.tif")
        self.row = {"gsm": "GSM1", "gse": "GSE1+GSE2", "published_gse": "GSE1",
                    "sample_dir": "samples/GSE1/GSM1", "platform": "Visium", "organism": "Homo sapiens",
                    "tissue": "brain", "n_obs": 10, "n_vars": 3, "has_image": True,
                    "has_spatial_um": False, "expression_sha256": self.expression_hash,
                    "image_sha256": self.image_hash, "expression_state": "counts",
                    "coordinate_units": "pixel", "split": "train"}
        self.meta = {"schema": "sda-release-sample-1", "gsm": "GSM1", "gse": ["GSE1", "GSE2"],
                     "published_gse": "GSE1", "split": "train", "expression": {"file": "spatial.h5ad",
                     "sha256": self.expression_hash, "n_obs": 10, "n_vars": 3, "state": "counts"},
                     "coordinates": {"units": "pixel", "spatial_um": False},
                     "image": {"file": "image.tif", "sha256": self.image_hash,
                               "spatial_to_pixel": [[2, 0, 10], [0, 3, 20], [0, 0, 1]]}}
        self.write_meta()
        self.catalog = Catalog([dict(self.row)])
        self.hub = patch("huggingface_hub.hf_hub_download", side_effect=self.fetch)
        self.mock_download = self.hub.start()
        self.addCleanup(self.hub.stop)

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_meta(self) -> None:
        (self.source / "sample.json").write_text(json.dumps(self.meta), encoding="utf-8")

    def fetch(self, repo: str, filename: str, **kwargs: object) -> str:
        self.assertEqual(kwargs["revision"], DEFAULT_REVISION)
        self.assertEqual(kwargs["repo_type"], "dataset")
        return str(self.source / Path(filename).name)

    def csv_path(self) -> Path:
        path = self.root / "catalog.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerow(self.row)
        return path

    def test_csv_types_and_boolean_false(self) -> None:
        catalog = Catalog.from_csv(self.csv_path(), revision=DEFAULT_REVISION)
        self.assertIs(catalog.get("GSM1")["has_spatial_um"], False)
        self.assertEqual(catalog.search(has_image=True, has_spatial_um=False).rows[0]["n_obs"], 10)
        self.assertEqual(len(catalog.catalog_sha256), 64)

    def test_search_membership_case_and_inclusive_ranges(self) -> None:
        self.assertEqual(len(self.catalog.search("BRAIN", gse="GSE2", platform="visium",
                                                min_obs=10, max_obs=10).rows), 1)
        self.assertEqual(len(self.catalog.search(gse="GSE20").rows), 0)
        self.assertEqual(len(self.catalog.search(max_obs=9).rows), 0)

    def test_unknown_filter_and_invalid_boolean_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.catalog.search(platfrom="Visium")
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.catalog.search(has_image="false")

    def test_duplicate_or_traversing_rows_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Catalog([dict(self.row), dict(self.row)])
        with self.assertRaisesRegex(ValueError, "directory"):
            Catalog([{**self.row, "sample_dir": "../../escaped"}])

    def test_empty_csv_requires_headers(self) -> None:
        path = self.root / "empty.csv"
        path.write_text("")
        with self.assertRaisesRegex(ValueError, "columns"):
            Catalog.from_csv(path, revision=DEFAULT_REVISION)

    def test_mutable_ref_resolved_once(self) -> None:
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.repo_info.return_value.sha = DEFAULT_REVISION
            self.mock_download.return_value = str(self.csv_path())
            self.mock_download.side_effect = None
            catalog = Catalog.from_hub(revision="main")
            self.assertEqual(catalog.revision, DEFAULT_REVISION)
            api.return_value.repo_info.assert_called_once()
            self.assertEqual(self.mock_download.call_args.kwargs["revision"], DEFAULT_REVISION)

    def test_offline_ref_never_resolves(self) -> None:
        with patch("huggingface_hub.HfApi") as api:
            with self.assertRaisesRegex(ValueError, "Offline"):
                Catalog.from_hub(revision="main", local_files_only=True)
            api.assert_not_called()

    def test_download_is_versioned_and_image_opt_in(self) -> None:
        directory = download_sample(self.catalog, "GSM1", self.root / "out")
        self.assertEqual(directory, self.root / "out" / DEFAULT_REVISION / "samples/GSE1/GSM1")
        self.assertFalse((directory / "image.tif").exists())
        verified = verify_sample(directory)
        self.assertTrue(verified["receipt_verified"])
        self.assertEqual(verified["revision"], DEFAULT_REVISION)
        with self.assertRaises(ValueError):
            verify_sample(directory, require_image=True)

    def test_reuse_avoids_matrix_download_and_preserves_image_receipt(self) -> None:
        directory = download_sample(self.catalog, "GSM1", self.root / "out", include_image=True)
        self.mock_download.reset_mock()
        download_sample(self.catalog, "GSM1", self.root / "out")
        self.assertEqual(self.mock_download.call_count, 0)
        self.assertIn("image.tif", verify_sample(directory)["files"])

    def test_corrupt_download_not_installed(self) -> None:
        (self.source / "spatial.h5ad").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            download_sample(self.catalog, "GSM1", self.root / "out")
        self.assertFalse((self.root / "out" / DEFAULT_REVISION / "samples/GSE1/GSM1/spatial.h5ad").exists())

    def test_conflicting_local_file_preserved(self) -> None:
        directory = download_sample(self.catalog, "GSM1", self.root / "out")
        (directory / "spatial.h5ad").write_bytes(b"user content")
        with self.assertRaisesRegex(ValueError, "Existing"):
            download_sample(self.catalog, "GSM1", self.root / "out")
        self.assertEqual((directory / "spatial.h5ad").read_bytes(), b"user content")

    def test_metadata_mismatch_stops_before_matrix(self) -> None:
        self.meta["expression"]["state"] = "unknown"
        self.write_meta()
        with self.assertRaisesRegex(ValueError, "disagrees"):
            download_sample(self.catalog, "GSM1", self.root / "out")
        self.assertEqual(self.mock_download.call_count, 1)

    def test_symlink_escape_rejected(self) -> None:
        output = self.root / "out"
        output.mkdir()
        (output / DEFAULT_REVISION).symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            download_sample(self.catalog, "GSM1", output)
        self.mock_download.assert_not_called()

    def test_local_metadata_tampering_detected_even_changed_gsm(self) -> None:
        directory = download_sample(self.catalog, "GSM1", self.root / "out")
        self.meta["gsm"] = "GSM99"
        (directory / "sample.json").write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError, "receipt"):
            verify_sample(directory)

    def test_selection_keeps_complete_provenance(self) -> None:
        path = self.root / "selection.json"
        self.catalog.search(gse="GSE2").save_selection(path)
        value = json.loads(path.read_text())
        self.assertEqual(value["revision"], DEFAULT_REVISION)
        self.assertEqual(value["samples"][0]["expression_sha256"], self.expression_hash)

    def test_cli_csv_export_not_truncated_by_display_limit(self) -> None:
        output = self.root / "selection.json"
        with redirect_stdout(io.StringIO()) as capture:
            status = main(["search", "--catalog-csv", str(self.csv_path()), "--offline",
                           "--where", "has_image=true", "--limit", "0", "--output", str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(capture.getvalue())["shown"], 0)
        self.assertEqual(len(json.loads(output.read_text())["samples"]), 1)

    def test_missing_id_download_never_contacts_hub(self) -> None:
        with self.assertRaisesRegex(ValueError, "absent"):
            download_sample(self.catalog, "GSM99", self.root / "out")
        self.mock_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
