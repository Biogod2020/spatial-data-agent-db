"""Inspect one downloaded sample; feature filtering is explicit and in memory."""

from __future__ import annotations

import argparse

from sda_db.io import coordinates, image_coordinates, read_sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--analysis-copy", action="store_true", help="Load an in-memory copy and remove drop_features")
    args = parser.parse_args()
    adata, meta = read_sample(args.directory)
    try:
        print("Shape:", adata.shape, "expression:", meta["expression"]["state"])
        print("Coordinate units and evidence:", meta["coordinates"])
        print("QC and review annotations:", meta["quality"])
        print("First native coordinates:", coordinates(adata, meta)[:3])
        if meta["image"] is not None:
            print("Level-0 image pixels:", image_coordinates(adata, meta)[:3])
        # Inspect available identifiers. Releases can use different feature ID vocabularies.
        drop = set(meta["quality"]["drop_features"])
        keep = ~adata.var_names.isin(drop)
        for column in ("source_feature_id", "feature_name"):
            keep &= ~adata.var[column].isin(drop).to_numpy()
        print("Features marked for removal:", int((~keep).sum()))
        if args.analysis_copy:
            analysis = adata[:, keep].to_memory()
            print("Analysis copy:", analysis.shape)
            # Continue with a method appropriate for meta['expression']['state'].
    finally:
        adata.file.close()


if __name__ == "__main__":
    main()
