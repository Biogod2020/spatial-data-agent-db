"""Inspect a TIFF pyramid and scale coordinates to an explicitly selected level."""

from __future__ import annotations

import argparse
from pathlib import Path

import tifffile

from sda_db.io import image_coordinates, read_sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--level", type=int, default=0)
    args = parser.parse_args()
    adata, meta = read_sample(args.directory)
    try:
        if meta["image"] is None:
            raise ValueError("No released image for this sample")
        with tifffile.TiffFile(args.directory / "image.tif") as image:
            levels = image.series[0].levels
            if not 0 <= args.level < len(levels):
                raise ValueError(f"Choose a level between 0 and {len(levels) - 1}")
            base, selected = levels[0], levels[args.level]
            width0, height0 = base.shape[base.axes.index("X")], base.shape[base.axes.index("Y")]
            width, height = selected.shape[selected.axes.index("X")], selected.shape[selected.axes.index("Y")]
            xy = image_coordinates(adata, meta) * [width / width0, height / height0]
            print("Pyramid levels:", [(level.axes, level.shape) for level in levels])
            print("Selected level coordinates:", xy[:3])
            # Only load a selected level when its dimensions suit your memory budget:
            # pixels = selected.asarray()
    finally:
        adata.file.close()


if __name__ == "__main__":
    main()
