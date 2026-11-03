"""Optional AnnData reading and explicit coordinate access; no automatic preprocessing."""

from __future__ import annotations

import json
from pathlib import Path
import re
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


def load(sample: str | Path, *, catalog: Any = None, release: str | None = None,
         destination: str | Path = "downloads", include_image: bool = False, backed: str | None = None,
         drop_features: bool = False, storage: str = "hub") -> Any:
    """One call from a GSM ID (or a downloaded sample directory) to an AnnData object.

    A GSM is downloaded (or reused after hash checks) at the catalog's release, then read.
    `adata.uns['sda_db']` records the GSM, release, commit and key sample.json fields;
    the full sample.json is `adata.uns['sda_db']['sample_json']` (a JSON string).
    drop_features=True removes the release's `quality.drop_features` in memory.
    Expression values, observations and coordinates are otherwise unchanged.
    """
    path = Path(sample) if not isinstance(sample, str) or not re.fullmatch(r"GSM[0-9]+", sample) else None
    if path is None:
        if catalog is None:
            from .catalog import Catalog

            catalog = Catalog.open(release)
        from .download import download_sample

        directory = download_sample(catalog, str(sample), destination, include_image=include_image, storage=storage)
        adata, metadata = read_sample(directory, backed=backed, verify=False)  # just verified by download
        release_name, revision = catalog.release_name, catalog.revision
    else:
        directory = path
        receipt = verify_sample(directory)
        adata, metadata = read_sample(directory, backed=backed, verify=False)
        release_name, revision = None, receipt.get("revision")
    if drop_features:
        keep = feature_mask(adata, metadata)
        if not keep.all():
            subset = adata[:, keep]
            adata = subset.to_memory() if backed else subset.copy()
    coords = metadata.get("coordinates") or {}
    image = metadata.get("image") or {}
    summary = {"gsm": metadata.get("gsm"), "published_gse": metadata.get("published_gse"),
               "release": release_name, "commit": revision, "directory": str(Path(directory).resolve()),
               "platform": (metadata.get("metadata") or {}).get("platform"),
               "organism": (metadata.get("metadata") or {}).get("organism"),
               "tissue": (metadata.get("metadata") or {}).get("tissue"),
               "expression_state": (metadata.get("expression") or {}).get("state"),
               "observation_unit": (metadata.get("expression") or {}).get("observation_unit"),
               "coordinate_units": coords.get("units"), "um_basis": coords.get("um_basis"),
               "has_image": bool(image), "split": metadata.get("split"),
               "sample_json": json.dumps(metadata, ensure_ascii=False)}
    if coords.get("um_per_unit") is not None:
        summary["um_per_unit"] = float(coords["um_per_unit"])
    if image:
        summary["image_stain"] = image.get("stain")
        if image.get("um_per_px") is not None:
            summary["image_um_per_px"] = float(image["um_per_px"])
    adata.uns["sda_db"] = {k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False))
                           for k, v in summary.items() if v is not None}  # scalars only: stays writable to .h5ad
    return adata


def feature_mask(adata: Any, metadata: dict[str, Any]) -> Any:
    """Boolean mask of features to keep after the release's explicit drop_features list."""
    import numpy as np

    drop = set((metadata.get("quality") or {}).get("drop_features") or [])
    keep = ~np.asarray(adata.var_names.isin(drop))
    for column in ("source_feature_id", "feature_name"):
        if column in adata.var:
            keep &= ~adata.var[column].isin(drop).to_numpy()
    return keep


def describe_sample(directory: str | Path, *, verify: bool = False) -> dict[str, Any]:
    """A compact, JSON-ready description of a downloaded sample (for CLIs and agents)."""
    import numpy as np

    adata, metadata = read_sample(directory, backed="r", verify=verify)
    try:
        coords = np.asarray(adata.obsm["spatial"])
        var = adata.var
        feature_types = (var["feature_type"].value_counts().to_dict() if "feature_type" in var else {})
        names = var["feature_name"] if "feature_name" in var else adata.var_names
        result = {
            "gsm": metadata.get("gsm"), "directory": str(Path(directory).resolve()),
            "shape": {"observations": int(adata.n_obs), "features": int(adata.n_vars)},
            "expression": metadata.get("expression"), "coordinates": metadata.get("coordinates"),
            "coordinate_range": {"min": coords.min(axis=0).round(3).tolist(),
                                 "max": coords.max(axis=0).round(3).tolist()} if len(coords) else None,
            "obs_columns": list(adata.obs.columns), "var_columns": list(var.columns),
            "obsm_keys": list(adata.obsm.keys()), "feature_types": {str(k): int(v) for k, v in feature_types.items()},
            "first_features": [str(n) for n in list(names[:10])],
            "quality": metadata.get("quality"), "split": metadata.get("split"),
            "image": None,
        }
    finally:
        if getattr(adata, "file", None) is not None:
            adata.file.close()
    if metadata.get("image") is not None:
        image = {k: v for k, v in metadata["image"].items() if k not in {"source"}}
        image["downloaded"] = (Path(directory) / metadata["image"]["file"]).is_file()
        if image["downloaded"]:
            try:
                from .image import image_levels

                image["levels"] = [{k: lv[k] for k in ("level", "width", "height", "um_per_px")}
                                   for lv in image_levels(directory)]
            except ImportError:
                pass
        result["image"] = image
    return result


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
