"""Pyramidal OME-TIFF access: levels, scaled reads, regions and patches (needs tifffile)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _tifffile() -> Any:
    try:
        import tifffile
    except ImportError as exc:
        raise ImportError("Install 'spatial-data-agent-db[analysis]' (tifffile) to read images") from exc
    return tifffile


def _image_path(directory: str | Path) -> tuple[Path, dict[str, Any]]:
    directory = Path(getattr(directory, "directory", directory))
    meta = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
    if meta.get("image") is None:
        raise ValueError(f"{meta.get('gsm')} has no released image")
    path = directory / meta["image"]["file"]
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; download the sample with include_image=True")
    return path, meta


def _dims(level: Any) -> tuple[int, int]:
    return level.shape[level.axes.index("X")], level.shape[level.axes.index("Y")]


def image_levels(directory: str | Path) -> list[dict[str, Any]]:
    """Width, height, axes, dtype and scale (relative to level 0) of every pyramid level."""
    path, meta = _image_path(directory)
    base_um = meta["image"].get("um_per_px")
    with _tifffile().TiffFile(path) as tif:
        levels = tif.series[0].levels
        width0, height0 = _dims(levels[0])
        result = []
        for index, level in enumerate(levels):
            width, height = _dims(level)
            sx, sy = width / width0, height / height0
            result.append({"level": index, "width": width, "height": height, "axes": level.axes,
                           "shape": tuple(level.shape), "dtype": str(level.dtype), "scale": (sx, sy),
                           "um_per_px": (base_um / sx) if isinstance(base_um, (int, float)) and sx else None})
    return result


def read_image(directory: str | Path, *, level: int | str = "auto", max_size: int = 2048,
               region: tuple[float, float, float, float] | None = None) -> tuple[Any, dict[str, Any]]:
    """Read one pyramid level, optionally only a region given in level-0 pixels (x0, y0, x1, y1).

    level='auto' picks the most detailed level whose width and height fit within
    `max_size` (for a region, the region's size at that level), or the smallest level
    if none fits. Returns the array and an `info` dict; map level-0 pixel coordinates
    (e.g. from `sda_db.io.image_coordinates`) into the returned array with `to_array_xy`.
    Regions are read tile-wise when zarr is installed.
    """
    import numpy as np

    path, meta = _image_path(directory)
    with _tifffile().TiffFile(path) as tif:
        levels = tif.series[0].levels
        width0, height0 = _dims(levels[0])
        if region is not None:
            x0, y0, x1, y1 = (float(v) for v in region)
            if not (x0 < x1 and y0 < y1):
                raise ValueError("region must be (x0, y0, x1, y1) with x0 < x1 and y0 < y1, in level-0 pixels")
            x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(width0), x1), min(float(height0), y1)
            if x0 >= x1 or y0 >= y1:
                raise ValueError("region lies outside the image")
        else:
            x0, y0, x1, y1 = 0.0, 0.0, float(width0), float(height0)
        if level == "auto":
            chosen = len(levels) - 1
            for index, candidate in enumerate(levels):
                width, height = _dims(candidate)
                if (x1 - x0) * width / width0 <= max_size and (y1 - y0) * height / height0 <= max_size:
                    chosen = index
                    break
        else:
            chosen = int(level)
            if not 0 <= chosen < len(levels):
                raise ValueError(f"level must be between 0 and {len(levels) - 1}")
        width, height = _dims(levels[chosen])
        sx, sy = width / width0, height / height0
        window = (int(np.floor(x0 * sx)), int(np.floor(y0 * sy)),
                  max(int(np.floor(x0 * sx)) + 1, int(np.ceil(x1 * sx))),
                  max(int(np.floor(y0 * sy)) + 1, int(np.ceil(y1 * sy))))
        array = _read_window(tif, chosen, window, whole=region is None)
        axes = levels[chosen].axes
    return array, _info(meta, chosen, axes, array, (sx, sy), window)


def _read_window(tif: Any, level: int, window: tuple[int, int, int, int], *, whole: bool = False) -> Any:
    """Pixels [y0:y1, x0:x1] of one level; tile-wise through zarr when available."""
    import numpy as np

    selected = tif.series[0].levels[level]
    x0, y0, x1, y1 = window
    index = [slice(None)] * len(selected.shape)
    index[selected.axes.index("Y")] = slice(max(0, y0), max(0, y1))
    index[selected.axes.index("X")] = slice(max(0, x0), max(0, x1))
    if not whole:
        try:
            import zarr

            return np.asarray(zarr.open(tif.series[0].aszarr(level=level), mode="r")[tuple(index)])
        except (ImportError, TypeError):  # no zarr, or a tifffile/zarr pairing without zarr-store support
            pass
    return selected.asarray()[tuple(index)]


def _info(meta: dict[str, Any], level: int, axes: str, array: Any, scale: tuple[float, float],
          window: tuple[int, int, int, int]) -> dict[str, Any]:
    sx, sy = scale
    base_um = meta["image"].get("um_per_px")
    return {"gsm": meta.get("gsm"), "level": level, "axes": axes, "shape": tuple(array.shape), "scale": scale,
            "array_origin": (max(0, window[0]), max(0, window[1])),
            "origin_level0": (max(0, window[0]) / sx, max(0, window[1]) / sy),
            "um_per_px": (base_um / sx) if isinstance(base_um, (int, float)) and sx else None,
            "stain": meta["image"].get("stain"), "channels": meta["image"].get("channels")}


def to_array_xy(xy_level0: Any, info: dict[str, Any]) -> Any:
    """Map level-0 pixel coordinates (n x 2, x then y) into a `read_image` result."""
    import numpy as np

    xy = np.asarray(xy_level0, dtype=float)
    sx, sy = info["scale"]
    ox, oy = info["array_origin"]
    return np.column_stack((xy[:, 0] * sx - ox, xy[:, 1] * sy - oy))


def thumbnail(directory: str | Path, *, max_size: int = 1024) -> tuple[Any, dict[str, Any]]:
    """The most detailed whole-slide level that fits in max_size x max_size."""
    return read_image(directory, level="auto", max_size=max_size)


def read_patch(directory: str | Path, center_xy: tuple[float, float], size: int, *,
               level: int = 0) -> tuple[Any, dict[str, Any]]:
    """A size x size pixel patch at `level`, centred on a level-0 pixel coordinate.

    Combine with `sda_db.io.image_coordinates` to cut patches around spots or cells.
    Patches at the slide border are clipped, not padded (check info['shape']).
    """
    path, meta = _image_path(directory)
    with _tifffile().TiffFile(path) as tif:
        levels = tif.series[0].levels
        if not 0 <= level < len(levels):
            raise ValueError(f"level must be between 0 and {len(levels) - 1}")
        width0, height0 = _dims(levels[0])
        width, height = _dims(levels[level])
        sx, sy = width / width0, height / height0
        x0 = math.floor(center_xy[0] * sx - size / 2 + 0.5)  # half-up: neighbouring centres never collide
        y0 = math.floor(center_xy[1] * sy - size / 2 + 0.5)
        window = (x0, y0, x0 + size, y0 + size)
        array = _read_window(tif, level, window)
        axes = levels[level].axes
    return array, _info(meta, level, axes, array, (sx, sy), window)
