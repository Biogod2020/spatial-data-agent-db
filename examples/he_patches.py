"""Build an image-patch / expression dataset from H&E samples (paired morphology and transcriptome).

Downloads each sample with its image (verified), cuts one patch per observation at a chosen pyramid level and
saves patches, expression rows and provenance to one .npz per sample. Bounded by --max-samples and --max-size.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import sda_db
from sda_db.image import read_patch
from sda_db.io import image_coordinates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="colorectal")
    parser.add_argument("--max-samples", type=int, default=2)
    parser.add_argument("--max-size", default="5GB", help="refuse larger downloads")
    parser.add_argument("--patch", type=int, default=224, help="patch size in pixels at --level")
    parser.add_argument("--level", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("patches"))
    args = parser.parse_args()
    catalog = sda_db.open_catalog()
    cohort = catalog.search(args.query, platform="Visium", image_stain="H&E", expression_state="counts",
                            where="image_registration!=FAILED").head(args.max_samples)
    report = cohort.download("downloads", include_image=True, max_bytes=args.max_size)
    args.output.mkdir(exist_ok=True)
    for gsm, directory in zip(cohort.gsms, report.directories):
        adata = sda_db.load(directory)
        meta = json.loads(adata.uns["sda_db"]["sample_json"])
        pixels = image_coordinates(adata, meta)
        patches, keep = [], []
        for index, xy in enumerate(pixels):
            patch, _ = read_patch(directory, xy, args.patch, level=args.level)
            if patch.shape[:2] == (args.patch, args.patch):  # drop clipped border patches
                patches.append(patch)
                keep.append(index)
        expression = adata.X[keep]
        np.savez_compressed(args.output / f"{gsm}.npz", patches=np.stack(patches),
                            expression=expression.toarray() if hasattr(expression, "toarray") else expression,
                            obs_names=np.asarray(adata.obs_names[keep]), var_names=np.asarray(adata.var_names),
                            provenance=json.dumps({"gsm": gsm, "release": catalog.release_name,
                                                   "commit": catalog.revision, "level": args.level,
                                                   "um_per_px_level0": meta["image"].get("um_per_px"),
                                                   "split": meta.get("split")}))
        print(f"{gsm}: {len(keep)} patches of {len(pixels)} observations, split={meta.get('split')}")


if __name__ == "__main__":
    main()
