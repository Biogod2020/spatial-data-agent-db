"""load(), feature masks, sample descriptions and pyramid reads on a synthetic sample."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

try:
    import anndata
    import numpy as np
    import pandas as pd
    import tifffile
    from scipy import sparse
except ImportError:  # pragma: no cover - optional analysis extra
    anndata = None

import fixtures


@unittest.skipIf(anndata is None, "Optional analysis dependencies are absent")
class SampleIoTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name) / "samples/GSE1/GSM1"
        self.directory.mkdir(parents=True)
        var = pd.DataFrame({"feature_name": ["A", "B", "TOTAL"], "source_feature_id": ["e1", "e2", "sum"],
                            "feature_type": ["Gene Expression"] * 3}, index=["e1", "e2", "sum"])
        obs = pd.DataFrame({"source_observation_id": ["s1", "s2", "s3"]}, index=["s1", "s2", "s3"])
        adata = anndata.AnnData(sparse.csr_matrix(np.array([[1, 0, 1], [0, 2, 2], [3, 0, 3]], dtype=np.int32)),
                                obs=obs, var=var)
        adata.obsm["spatial"] = np.array([[10.0, 10.0], [40.0, 20.0], [70.0, 50.0]])
        adata.write_h5ad(self.directory / "spatial.h5ad")
        image = np.zeros((64, 96, 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(96, dtype=np.uint8)[None, :]
        with tifffile.TiffWriter(self.directory / "image.tif") as tif:
            tif.write(image, subifds=1, tile=(16, 16), photometric="rgb")
            tif.write(image[::2, ::2], subfiletype=1, tile=(16, 16), photometric="rgb")
        self.meta = {"schema": "sda-release-sample-1", "gsm": "GSM1", "gse": ["GSE1"], "published_gse": "GSE1",
                     "split": "train", "metadata": {"platform": "Visium", "organism": "Homo sapiens", "tissue": "x"},
                     "expression": {"file": "spatial.h5ad", "sha256": fixtures.digest(
                         (self.directory / "spatial.h5ad").read_bytes()), "n_obs": 3, "n_vars": 3,
                         "state": "counts", "observation_unit": "spot"},
                     "coordinates": {"units": "pixel", "um_basis": "platform_constant", "um_per_unit": 2.0},
                     "image": {"file": "image.tif", "sha256": fixtures.digest((self.directory / "image.tif").read_bytes()),
                               "stain": "H&E", "channels": ["R", "G", "B"], "um_per_px": 0.5,
                               "spatial_to_pixel": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
                     "quality": {"flags": [], "qc_flags": [], "drop_features": ["sum"], "review": None}}
        (self.directory / "sample.json").write_text(json.dumps(self.meta))

    def test_load_directory_records_metadata_and_drops_listed_features(self) -> None:
        from sda_db import load

        adata = load(self.directory)
        self.assertEqual(adata.shape, (3, 3))
        info = adata.uns["sda_db"]
        self.assertEqual((info["gsm"], info["coordinate_units"], info["um_per_unit"]), ("GSM1", "pixel", 2.0))
        self.assertEqual(json.loads(info["sample_json"])["gsm"], "GSM1")
        self.assertNotIn("commit", info)  # no download receipt: provenance is not claimed
        trimmed = load(self.directory, drop_features=True)
        self.assertEqual(list(trimmed.var_names), ["e1", "e2"])
        backed = load(self.directory, backed="r", drop_features=True)
        self.assertEqual(backed.shape, (3, 2))
        trimmed.write_h5ad(Path(self.directory.parent) / "roundtrip.h5ad")  # uns stays writable

    def test_load_gsm_downloads_through_the_catalog(self) -> None:
        from sda_db import load

        catalog = fixtures.catalog()
        with patch("sda_db.download.download_sample", return_value=self.directory) as fetched:
            adata = load("GSM1", catalog=catalog, destination="somewhere")
        fetched.assert_called_once()
        self.assertEqual(adata.uns["sda_db"]["commit"], catalog.revision)

    def test_describe_sample(self) -> None:
        from sda_db.io import describe_sample

        info = describe_sample(self.directory, verify=True)
        self.assertEqual(info["shape"], {"observations": 3, "features": 3})
        self.assertEqual(info["coordinate_range"], {"min": [10.0, 10.0], "max": [70.0, 50.0]})
        self.assertEqual([lv["width"] for lv in info["image"]["levels"]], [96, 48])
        self.assertNotIn("source", info["image"])

    def test_pyramid_levels_regions_and_patches(self) -> None:
        from sda_db.image import image_levels, read_image, read_patch, thumbnail, to_array_xy

        levels = image_levels(self.directory)
        self.assertEqual([(lv["width"], lv["height"]) for lv in levels], [(96, 64), (48, 32)])
        self.assertEqual(levels[1]["um_per_px"], 1.0)
        whole, info = thumbnail(self.directory, max_size=50)
        self.assertEqual((whole.shape, info["level"]), ((32, 48, 3), 1))
        region, info = read_image(self.directory, region=(20, 8, 52, 40), level=0)
        self.assertEqual(region.shape, (32, 32, 3))
        self.assertEqual(int(region[0, 0, 0]), 20)  # red channel encodes x
        np.testing.assert_allclose(to_array_xy([[20, 8]], info), [[0, 0]])
        patch_, pinfo = read_patch(self.directory, (40.5, 20.0), 16, level=1)
        self.assertEqual(patch_.shape, (16, 16, 3))
        self.assertEqual(pinfo["level"], 1)
        origins = [read_patch(self.directory, (cx, 20), 3)[1]["array_origin"][0] for cx in (10, 11, 12)]
        self.assertEqual(origins, [9, 10, 11])  # half-up rounding: one pixel per pixel of centre
        with self.assertRaises(ValueError):
            read_image(self.directory, region=(200, 200, 300, 300))
        with self.assertRaises(ValueError):
            read_image(self.directory, level=5)


if __name__ == "__main__":
    unittest.main()
