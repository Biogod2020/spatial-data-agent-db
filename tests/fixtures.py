"""Synthetic catalog fixtures shared by the offline tests (not scientific samples)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sda_db import Catalog

STUDIES = [
    {"gse": "GSE10", "title": "Spatial atlas of Alzheimer's disease hippocampus", "summary": "Human brain tissue.",
     "design": "Visium of donors with and without dementia", "pubmed_ids": ["111"]},
    {"gse": "GSE20", "title": "Mouse kidney injury time course", "summary": "Kidney & fibrosis in mice.",
     "design": "Xenium panel", "pubmed_ids": []},
    {"gse": "GSE30", "title": "Breast tumour microenvironment", "summary": "Tumors {with} 50% stroma_cells.",
     "design": "Visium HD", "pubmed_ids": ["333"]},
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row(gsm: str, gse: str, **values: Any) -> dict[str, Any]:
    base = {"gsm": gsm, "gse": gse, "published_gse": gse.split("+")[0], "sample_dir": f"samples/{gse.split('+')[0]}/{gsm}",
            "source_pipeline": "sda-all", "platform": "Visium", "technology": "Visium", "organism": "Homo sapiens",
            "tissue": "", "observation_unit": "spot", "n_obs": 100, "n_vars": 50, "expression_state": "counts",
            "verification": "VERIFIED", "expression_sha256": digest(gsm.encode()), "coordinate_units": "pixel",
            "um_basis": "unresolved", "um_per_unit": None, "has_spatial_um": False, "has_image": False,
            "image_stain": "", "image_um_per_px": None, "image_registration": "", "image_sha256": "",
            "flags": "", "qc_flags": "", "drop_features": "", "review_decision": "", "aliases": "",
            "content_group": gsm, "split": "train"}
    base.update(values)
    if base["has_image"] and not base["image_sha256"]:
        base["image_sha256"] = digest((gsm + "image").encode())
    return base


def rows() -> list[dict[str, Any]]:
    return [
        row("GSM1", "GSE10", tissue="hippocampus", n_obs=5000, has_image=True, image_stain="H&E"),
        row("GSM2", "GSE10", tissue="frontal cortex", n_obs=4000, has_image=True, image_stain="H&E",
            aliases="GSM9;GSM8"),
        row("GSM3", "GSE20+GSE21", platform="Xenium", organism="Mus musculus", tissue="kidney", n_obs=200000,
            observation_unit="cell", coordinate_units="um", has_image=True, image_stain="fluorescence: DAPI",
            split="test", flags="units_unstated;attested"),
        row("GSM4", "GSE30", platform="Visium HD", tissue="breast tumour", n_obs=90000, observation_unit="bin",
            split="val"),
        row("GSM5", "GSE30", platform="Visium HD", organism="Homo sapiens\nMus musculus", tissue="xenograft",
            n_obs=80000, observation_unit="bin", split="val"),
    ]


SAMPLE_RECORDS = [
    {"gsm": "GSM1", "disease": "Alzheimer's disease", "sex": "female", "publication_pmid": "111",
     "counts_ready": "VERIFIED"},
    {"gsm": "GSM2", "disease": None, "sex": "male", "publication_pmid": "111", "counts_ready": "VERIFIED"},
    {"gsm": "GSM3", "disease": "acute kidney injury", "publication_doi": "10.1/abc", "counts_ready": "VERIFIED"},
    {"gsm": "GSM4", "disease": "breast cancer", "counts_ready": "ABSENT"},
]


def catalog() -> Catalog:
    result = Catalog(rows())
    result._attach_metadata(STUDIES, SAMPLE_RECORDS)
    return result


def write_catalog_files(root: Path) -> dict[str, Path]:
    """Write samples.csv and the atlas JSONL files as a release would publish them."""
    import csv

    root.mkdir(parents=True, exist_ok=True)
    paths = {"catalog/samples.csv": root / "samples.csv",
             "catalog/atlas-study-metadata.jsonl": root / "studies.jsonl",
             "catalog/atlas-sample-metadata.jsonl": root / "samples.jsonl"}
    data = rows()
    with paths["catalog/samples.csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader()
        for item in data:
            writer.writerow({k: ("" if v is None else v) for k, v in item.items()})
    paths["catalog/atlas-study-metadata.jsonl"].write_text("".join(json.dumps(s) + "\n" for s in STUDIES))
    paths["catalog/atlas-sample-metadata.jsonl"].write_text("".join(json.dumps(s) + "\n" for s in SAMPLE_RECORDS))
    return paths
