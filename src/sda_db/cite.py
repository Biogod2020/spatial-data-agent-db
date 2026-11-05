"""Citations for the database, the release and every original GEO series of a selection."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .query import members

if TYPE_CHECKING:
    from .catalog import Catalog

# Crossref record for the preprint, retrieved 2026-11-02 (DOI 10.64898/2026.05.27.727615).
PAPER = {
    "key": "ji2026spatialdataagent",
    "type": "article",
    "title": "An agent-built, verified atlas of public spatial transcriptomics shows that breadth across "
             "studies drives transfer and reproducibility",
    "authors": ["Ji, Jia-Hao", "Zou, Qi", "Cheng, Jiabei", "She, Zixi", "Hao, Yining", "Liu, Weishi",
                "Zhang, Daoliang", "Wang, Zhikang", "Yu, Jin-Tai", "Yuan, Zhiyuan"],
    "journal": "bioRxiv",
    "year": "2026",
    "doi": "10.64898/2026.05.27.727615",
    "url": "https://doi.org/10.64898/2026.05.27.727615",
}
_SPECIAL = re.compile(r"([&%$#_{}])")


def _escape(text: str) -> str:
    return _SPECIAL.sub(r"\\\1", " ".join(str(text).split()))


def entries(catalog: Catalog) -> list[dict[str, Any]]:
    """Structured citation records: paper, dataset release, then one per GEO series."""
    release = catalog.release
    dataset = {
        "key": f"sdadb_{catalog.release_name.replace('.', '_').replace('-', '_')}",
        "type": "misc",
        "title": "SpatialDataAgent-DB",
        "howpublished": f"Hugging Face dataset {catalog.repo_id}",
        "version": catalog.release_name,
        "note": f"Release {catalog.release_name}, commit {catalog.revision}"
                + (f", tag {release.tag}" if release and release.tag else "")
                + (f" (data of release {release.data_release})" if release and release.data_release else ""),
        "url": f"https://huggingface.co/datasets/{catalog.repo_id}/tree/{catalog.revision}",
        "year": "2026",
    }
    series: dict[str, dict[str, Any]] = {}
    for row in catalog.rows:
        for gse in members("gse", row["gse"]):
            item = series.setdefault(gse, {"samples": 0, "dois": set(), "pmids": set()})
            item["samples"] += 1
            if row.get("publication_doi"):
                item["dois"].add(row["publication_doi"])
            if row.get("publication_pmid"):
                item["pmids"].add(str(row["publication_pmid"]))
            if gse == row["published_gse"]:  # metadata v2: PubMed IDs of the series and the sample record
                item["pmids"].update(members("pubmed_ids", row.get("pubmed_ids")))
    result = [dict(PAPER), dataset]
    metadata = catalog.metadata_v2
    if metadata:
        repo = metadata.get("gated_repo_id") or catalog.repo_id
        commit = metadata.get("gated_revision") or metadata["revision"]
        folder = metadata["path"].rsplit("/", 1)[0]
        result.append({
            "key": f"sdadb_{metadata['tag'].replace('.', '_').replace('-', '_')}",
            "type": "misc",
            "title": "SpatialDataAgent-DB curated sample metadata v2",
            "howpublished": f"Hugging Face dataset {repo}, {folder}",
            "version": metadata["tag"],
            "note": f"Metadata {metadata.get('schema')}, tag {metadata['tag']}, commit {commit}; "
                    f"{metadata['path'].rsplit('/', 1)[1]} SHA256 {metadata['sha256']}",
            "url": f"https://huggingface.co/datasets/{repo}/tree/{metadata['tag']}/{folder}",
            "year": "2026",
        })
    for gse in sorted(series, key=lambda g: int(g[3:])):
        study = catalog.studies.get(gse, {})
        pmids = sorted(set(map(str, study.get("pubmed_ids") or [])) | series[gse]["pmids"])
        dois = sorted(series[gse]["dois"])
        count = series[gse]["samples"]
        note = [f"GEO accession {gse}", f"{count} sample{'s' if count != 1 else ''} in this selection"]
        if pmids:
            note.append("PMID " + ", ".join(pmids))
        record = {"key": f"geo_{gse}", "type": "misc", "title": study.get("title") or f"GEO series {gse}",
                  "howpublished": "Gene Expression Omnibus", "note": "; ".join(note),
                  "url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}", "gse": gse,
                  "pmids": pmids, "dois": dois}
        if len(dois) == 1:
            record["doi"] = dois[0]
        result.append(record)
    return result


def _bibtex(record: dict[str, Any]) -> str:
    lines = [f"@{record['type']}{{{record['key']},"]
    if record.get("authors"):
        lines.append(f"  author       = {{{' and '.join(record['authors'])}}},")
    for name in ("title", "journal", "howpublished", "version", "year", "doi", "url", "note"):
        if record.get(name):
            value = record[name] if name in {"url", "doi"} else _escape(record[name])
            lines.append(f"  {name:<12} = {{{{{value}}}}}," if name == "title" else f"  {name:<12} = {{{value}}},")
    lines[-1] = lines[-1].rstrip(",")
    return "\n".join(lines) + "\n}"


def cite(catalog: Catalog, *, format: str = "bibtex") -> str:
    """BibTeX (default), plain text or JSON citations for a selection."""
    records = entries(catalog)
    if format == "json":
        return json.dumps(records, ensure_ascii=False, indent=2)
    if format == "text":
        lines = []
        for record in records:
            if record["key"] == PAPER["key"]:
                lines.append(f"{', '.join(PAPER['authors'][:3])} et al. {PAPER['title']}. bioRxiv (2026). "
                             f"doi:{PAPER['doi']}")
            elif record["type"] == "misc" and record.get("gse"):
                extra = f" PMID {', '.join(record['pmids'])}." if record["pmids"] else ""
                lines.append(f"{record['gse']}: {record['title']}.{extra} {record['url']}")
            else:
                lines.append(f"SpatialDataAgent-DB, {record['note']}. {record['url']}")
        return "\n".join(lines)
    if format != "bibtex":
        raise ValueError("format must be bibtex, text or json")
    header = (f"% Citations for {len(catalog)} samples from SpatialDataAgent-DB release {catalog.release_name} "
              f"(commit {catalog.revision}"
              + (f"; metadata {catalog.metadata_v2['tag']}" if catalog.metadata_v2 else "")
              + f").\n% Cite the original studies as well as the database; check each "
              "study's data-use terms.\n")
    return header + "\n\n".join(_bibtex(r) for r in records) + "\n"
