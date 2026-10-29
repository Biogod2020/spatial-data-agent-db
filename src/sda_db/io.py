"""Optional AnnData reading and explicit coordinate access; no automatic preprocessing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .download import verify_sample


def read_sample(directory: str | Path, *, backed: str | None = "r",
                verify: bool = True) -> tuple[Any, dict[str, Any]]:
    """Read expression plus metadata; backed='r' limits memory for large files."""
    if backed not in {None, "r"}:
        raise ValueError("Use backed='r' or None for read-only database access")
    try:
        import anndata
    except ImportError as exc:
        raise ImportError("Install 'spatial-data-agent-db[analysis]' to read AnnData") from exc
    directory = Path(directory)
    if verify:
        verify_sample(directory)
    metadata = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
    return anndata.read_h5ad(directory / "spatial.h5ad", backed=backed), metadata


def coordinates(adata: Any, metadata: dict[str, Any], *, units: str = "native") -> Any:
    """Return native or recorded micrometre coordinates without inferring a scale."""
    if units == "native":
        return adata.obsm["spatial"]
    if units != "um":
        raise ValueError("units must be 'native' or 'um'")
    if metadata["coordinates"]["units"] == "um":
        return adata.obsm["spatial"]
    if metadata["coordinates"].get("spatial_um") and "spatial_um" in adata.obsm:
        return adata.obsm["spatial_um"]
    raise ValueError("No recorded micrometre coordinates; inspect coordinates.um_basis in sample.json")


def image_coordinates(adata: Any, metadata: dict[str, Any]) -> Any:
    """Apply the released transform to native x/y; return level-0 image pixels.

    Pyramid levels need separate width/height scaling. A 3D spatial coordinate
    requires an explicit projection and is not silently treated as a 2D slide.
    """
    import numpy as np

    image = metadata.get("image")
    if image is None:
        raise ValueError("This sample has no released image")
    xy = np.asarray(adata.obsm["spatial"])
    transform = np.asarray(image["spatial_to_pixel"], dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2 or transform.shape != (3, 3):
        raise ValueError("Image projection requires n-by-2 coordinates and a 3-by-3 transform")
    mapped = np.column_stack((xy, np.ones(len(xy)))) @ transform.T
    if not np.isfinite(mapped).all() or np.any(mapped[:, 2] == 0):
        raise ValueError("Invalid homogeneous image coordinates")
    return mapped[:, :2] / mapped[:, 2, None]
