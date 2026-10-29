"""Coordinate semantics without loading an AnnData object."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from sda_db.io import coordinates, image_coordinates


@unittest.skipIf(np is None, "Optional analysis dependencies are absent")
class CoordinateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adata = SimpleNamespace(obsm={"spatial": np.array([[1, 2], [3, 4]])})
        self.meta = {"coordinates": {"units": "pixel", "spatial_um": False},
                     "image": {"spatial_to_pixel": [[2, 0, 10], [0, 3, 20], [0, 0, 1]]}}

    def test_unresolved_scale_never_inferred(self) -> None:
        with self.assertRaisesRegex(ValueError, "No recorded"):
            coordinates(self.adata, self.meta, units="um")

    def test_known_um_key_selected(self) -> None:
        self.meta["coordinates"]["spatial_um"] = True
        self.adata.obsm["spatial_um"] = self.adata.obsm["spatial"] * 0.5
        np.testing.assert_array_equal(coordinates(self.adata, self.meta, units="um"), [[0.5, 1], [1.5, 2]])

    def test_transform_uses_native_xy_and_homogeneous_division(self) -> None:
        np.testing.assert_array_equal(image_coordinates(self.adata, self.meta), [[12, 26], [16, 32]])
        self.meta["image"]["spatial_to_pixel"][2][2] = 2
        np.testing.assert_array_equal(image_coordinates(self.adata, self.meta), [[6, 13], [8, 16]])

    def test_three_dimensional_coordinates_require_projection(self) -> None:
        self.adata.obsm["spatial"] = np.zeros((2, 3))
        with self.assertRaisesRegex(ValueError, "n-by-2"):
            image_coordinates(self.adata, self.meta)
