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


def feature_index_file(root: Path, catalog_sha256: str = "") -> Path:
    """A synthetic feature index: GSM1/GSM2 share a human panel, GSM3 is a mouse panel, GSM4 differs."""
    from sda_db.features import write_index

    human = [["GFAP", "ENSG00000131095", "Gene Expression"], ["AQP4", "ENSG00000171885.15", "Gene Expression"]]
    mouse = [["Gfap", "ENSMUSG00000020932", "Gene Expression"], ["Snap25", "ENSMUSG00000027273", "Gene Expression"]]
    other = [["ERBB2", "ENSG00000141736", "Gene Expression"], ["CD3E_protein", "CD3E", "Antibody Capture"],
             ["GRCh38_ESR1", "GRCh38_ENSG00000091831", "Gene Expression"]]
    whole = [[f"G{i}", f"ID{i}", "Gene Expression"] for i in range(30)] + [["GFAP", "ENSG00000131095.9",
                                                                             "Gene Expression"]]
    path = root / f"feature-index-{catalog_sha256[:4] or 'x'}.sqlite"
    write_index([human, mouse, other, whole], {"GSM1": 0, "GSM2": 0, "GSM3": 1, "GSM4": 2, "GSM5": 3}, path,
                catalog_sha256=catalog_sha256, targeted_max=10)
    return path


# --------------------------------------------------------------------------- curated metadata v2
V2_COLUMNS = ("gsm", "published_gse", "platform", "organism", "sex", "review_category", "review_notes", "organ",
              "organ_source", "tissue_ontology_id", "tissue_ontology_name", "tissue_raw", "sample_kind",
              "disease_status", "disease_status_source", "disease_ontology_ids", "disease_ontology_names",
              "disease_raw", "condition_raw", "life_stage", "life_stage_source", "life_stage_luna", "age_raw",
              "sex_raw", "donor_key", "donor_id_raw", "organism_taxon_id", "organism_common_name", "organisms",
              "organism_taxon_ids", "co_detected_organisms", "xenograft", "multi_species", "organism_basis",
              "organism_raw", "feature_species", "preservation", "preservation_raw", "platform_basis", "assay_name",
              "assay_type", "technology_raw", "panel_name", "instrument_software", "pubmed_ids", "dois",
              "annotation_version", "annotation_confidence", "annotation_low_confidence", "metadata_flags", "n_obs")


def _v2(gsm: str, gse: str, *, confidence: dict[str, float] | None = None, **values: Any) -> dict[str, str]:
    scores = {"organ": 1.0, "sample_kind": 1.0, "disease_status": 1.0, "uberon": 0.95, "life_stage": 1.0}
    scores.update(confidence or {})
    base = {k: "" for k in V2_COLUMNS}
    base.update(gsm=gsm, published_gse=gse, platform="Visium", organism="Homo sapiens", sex="unknown",
                organ_source="luna", disease_status_source="luna", life_stage="not stated", life_stage_source="luna",
                organism_taxon_id="9606", organism_common_name="human", organisms="Homo sapiens",
                organism_taxon_ids="9606", xenograft="False", multi_species="False", organism_basis="GEO organism field",
                platform_basis="release v3", assay_name="Visium", assay_type="sequencing-based",
                annotation_version="sda-tissue-disease-2.3", annotation_confidence=json.dumps(scores), n_obs="100")
    low = [k for k, v in scores.items() if v < 0.6]
    base["annotation_low_confidence"] = ";".join(low)
    flags = (["low_confidence_annotation"] if low else []) + values.pop("flags", [])
    base["metadata_flags"] = ";".join(flags)
    base.update({k: str(v) for k, v in values.items()})
    return base


def metadata_v2_rows() -> list[dict[str, str]]:
    """Synthetic metadata v2 for the five fixture samples, CSV-encoded as published."""
    return [
        _v2("GSM1", "GSE10", organ="brain", tissue_ontology_id="UBERON:0002421",
            tissue_ontology_name="hippocampal formation", sample_kind="primary tissue", disease_status="disease",
            disease_ontology_ids="MONDO:0004975", disease_ontology_names="Alzheimer disease", life_stage="aged",
            sex="female", preservation="FFPE", assay_name="Visium CytAssist", pubmed_ids="111;999"),
        _v2("GSM2", "GSE10", organ="brain", tissue_ontology_id="UBERON:0001870",
            tissue_ontology_name="frontal cortex", sample_kind="primary tissue", disease_status="normal",
            life_stage="aged", sex="male", preservation="fresh frozen", confidence={"disease_status": 0.5},
            flags=["normal_but_disease_text"]),
        _v2("GSM3", "GSE20", platform="Xenium", organism="Mus musculus", organ="kidney",
            tissue_ontology_id="UBERON:0002113", tissue_ontology_name="kidney", sample_kind="primary tissue",
            disease_status="disease", disease_ontology_ids="MONDO:0002492", disease_ontology_names="acute kidney failure",
            life_stage="adult", organism_taxon_id="10090", organism_common_name="house mouse", organisms="Mus musculus",
            organism_taxon_ids="10090", assay_name="Xenium", assay_type="imaging-based", preservation="fixed frozen"),
        _v2("GSM4", "GSE30", platform="Visium HD", organ="breast", tissue_ontology_id="UBERON:0000310",
            tissue_ontology_name="breast", sample_kind="tumour tissue", disease_status="disease",
            disease_ontology_ids="MONDO:0007254", disease_ontology_names="breast cancer", assay_name="Visium HD",
            preservation="FFPE", confidence={"uberon": 0.7}),
        _v2("GSM5", "GSE30", platform="Visium", platform_basis="GEO series text", organ="breast",
            sample_kind="xenograft", disease_status="disease", disease_ontology_ids="MONDO:0007254;MONDO:0006256",
            disease_ontology_names="breast cancer;invasive breast carcinoma", organisms="Homo sapiens;Mus musculus",
            organism_taxon_ids="9606;10090", xenograft="True", multi_species="True", feature_species="Mus musculus",
            assay_name="Visium", flags=["feature_species_differs"]),
    ]


def write_metadata_v2(path: Path, data: list[dict[str, str]] | None = None) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(V2_COLUMNS))
        writer.writeheader()
        writer.writerows(metadata_v2_rows() if data is None else data)
    return path


def metadata_v2_info(path: Path) -> dict[str, Any]:
    return {"schema": "sda-sample-metadata-2", "tag": "metadata-v2.1-20261105",
            "repo_id": "Biogod/SpatialDataAgent-DB-Catalog", "revision": "a" * 40,
            "gated_repo_id": "Biogod/SpatialDataAgent-DB", "gated_revision": "b" * 40,
            "path": "metadata/v2/samples_metadata_v2.csv", "sha256": digest(path.read_bytes())}


def catalog_v2(root: Path) -> Catalog:
    from sda_db.catalog import read_metadata_v2

    path = write_metadata_v2(root / "samples_metadata_v2.csv")
    result = catalog()
    result._attach_metadata_v2(read_metadata_v2(path), metadata_v2_info(path))
    return result
