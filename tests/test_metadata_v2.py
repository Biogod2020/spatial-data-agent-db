"""Curated metadata v2: SHA-bound loading, the gsm join, filters, ranking, cards, citations, CLI and MCP,
and the paper release staying exactly as published."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from sda_db import Catalog, IntegrityError, UnknownFieldError, UnknownValueError, resolve_release
from sda_db.catalog import read_metadata_v2
from sda_db.cli import main
from sda_db.mcp_server import Server
from sda_db.releases import named_release, recorded_release

import fixtures

PUBLIC = "Biogod/SpatialDataAgent-DB-Catalog"
V2_PATH = "metadata/v2/samples_metadata_v2.csv"


class Offline(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)


class RegistryTests(Offline):
    def test_default_release_is_paper_data_plus_metadata_v2(self) -> None:
        default, paper = resolve_release(None), resolve_release("paper")
        self.assertEqual(default.name, "v1.1.1-20261106")
        for alias in ("metadata-v2", "metadata-v2.2.1", "v1.1", "v1.1.1"):
            self.assertEqual(resolve_release(alias).name, default.name)
        self.assertEqual(default.revision, paper.revision)
        self.assertEqual(default.files, paper.files)  # same catalog bytes
        self.assertEqual(default.feature_index, paper.feature_index)
        self.assertEqual(default.data_release, "v1.0.0-20261025")
        meta = default.metadata
        self.assertEqual((meta["tag"], meta["path"], meta["schema"]),
                         ("metadata-v2.2.1-20261106", V2_PATH, "sda-sample-metadata-2"))
        self.assertEqual(meta["files"][V2_PATH], meta["sha256"])
        self.assertTrue(all(len(v) == 64 for v in meta["files"].values()))
        self.assertIsNone(paper.metadata)
        self.assertEqual(resolve_release("v1.0.0-20261025").name, paper.name)

    def test_client_040_release_stays_resolvable(self) -> None:
        old = resolve_release("v1.1.0-20261105")
        self.assertEqual(resolve_release("metadata-v2.1").name, old.name)
        self.assertEqual(resolve_release("v1.1.0").name, old.name)
        self.assertEqual((old.revision, old.files), (resolve_release(None).revision, resolve_release(None).files))
        self.assertEqual((old.metadata["tag"], old.metadata["revision"], old.metadata["sha256"]),
                         ("metadata-v2.1-20261105", "0ddfdbfeb5e19e6d35fcfa8fe46596631675e28e",
                          "62f36ce967cabb80398f141b8b2beb981ff97a89a14d3a49d7577bb59d28f12e"))
        self.assertEqual(recorded_release({"release": old.name, "revision": old.revision}), old.name)

    def test_latest_still_means_the_rolling_release(self) -> None:
        self.assertIsNone(named_release("latest"))
        with self.assertRaisesRegex(Exception, "Offline"):
            resolve_release("latest", offline=True)

    def test_saved_selections_reopen_their_named_release(self) -> None:
        commit = resolve_release(None).revision
        self.assertEqual(recorded_release({"release": "v1.1.1-20261106", "revision": commit}), "v1.1.1-20261106")
        self.assertEqual(recorded_release({"release": "v1.0.0-20261025", "revision": commit}), "v1.0.0-20261025")
        self.assertEqual(recorded_release({"release": "v1.1.1-20261106", "revision": "c" * 40}), "c" * 40)
        self.assertEqual(recorded_release({"release": "abc1234", "revision": "c" * 40}), "c" * 40)


class OpenTests(Offline):
    """Catalog.open() for the default release: v3 files and the v2 table, each bound by its SHA256."""

    def setUp(self) -> None:
        super().setUp()
        self.paths = fixtures.write_catalog_files(self.root / "catalog")
        self.paths[V2_PATH] = fixtures.write_metadata_v2(self.root / "v2" / "samples_metadata_v2.csv")
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, repo: str, filename: str, **kwargs: object) -> str:
        self.calls.append((repo, filename, str(kwargs["revision"])))
        return str(self.paths[filename])

    def release(self, name: str | None = None, **metadata: object):
        release = resolve_release(name)
        files = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in self.paths.items() if k != V2_PATH}
        changes: dict[str, object] = {"files": files}
        if release.metadata:
            changes["metadata"] = {**release.metadata,
                                   "sha256": hashlib.sha256(self.paths[V2_PATH].read_bytes()).hexdigest(), **metadata}
        return type(release)(**{**release.__dict__, **changes})

    def open(self, name: str | None = None, *, source: str = "auto", **metadata: object) -> Catalog:
        with patch("huggingface_hub.hf_hub_download", side_effect=self.fetch), \
                patch("sda_db.catalog.resolve_release", return_value=self.release(name, **metadata)):
            return Catalog.open(name, source=source)

    def test_join_adds_v2_and_keeps_v3_values(self) -> None:
        catalog = self.open()
        self.assertIn((PUBLIC, V2_PATH, "2511bb73cdbebffe393c3d8f1b136183049b435a"), self.calls)
        self.assertEqual(catalog.metadata_v2["tag"], "metadata-v2.2.1-20261106")
        gsm1, gsm5 = catalog.get("GSM1"), catalog.get("GSM5")
        self.assertEqual((gsm1["organ"], gsm1["tissue_ontology_id"], gsm1["disease_ontology_ids"]),
                         ("brain", "UBERON:0002421", "MONDO:0004975"))
        self.assertEqual((gsm1["disease"], gsm1["study_title"], gsm1["tissue"]),  # v3 fields are kept
                         ("Alzheimer's disease", "Spatial atlas of Alzheimer's disease hippocampus", "hippocampus"))
        self.assertEqual((gsm5["platform"], gsm5["platform_v3"]), ("Visium", "Visium HD"))  # v2 correction
        self.assertEqual((gsm5["organism"], gsm5["organism_v3"]), ("Homo sapiens", "Homo sapiens\nMus musculus"))
        self.assertEqual((catalog.get("GSM2")["sex"], catalog.get("GSM2")["sex_v3"]), ("male", "male"))
        self.assertEqual((catalog.get("GSM3")["sex"], catalog.get("GSM3")["sex_v3"]), ("unknown", None))
        self.assertIs(gsm5["xenograft"], True)
        self.assertIsNone(gsm1["co_detected_organisms"])
        self.assertEqual(catalog.get("GSM2")["annotation_min_confidence"], 0.5)
        self.assertEqual(len(catalog), 5)

    def test_gated_source_reads_metadata_from_the_gated_tag_commit(self) -> None:
        catalog = self.open(source="gated")
        self.assertIn(("Biogod/SpatialDataAgent-DB", V2_PATH, "b90f301351983ed4bdb13550fda25320ccfc98ba"), self.calls)
        self.assertEqual(catalog.get("GSM4")["organ"], "breast")

    def test_paper_release_is_unchanged(self) -> None:
        paper = self.open("paper")
        self.assertNotIn(V2_PATH, [name for _, name, _ in self.calls])
        self.assertIsNone(paper.metadata_v2)
        row = paper.get("GSM5")
        for name in ("organ", "disease_status", "platform_v3", "annotation_min_confidence"):
            self.assertNotIn(name, row)
        self.assertEqual((row["platform"], row["organism"]), ("Visium HD", "Homo sapiens\nMus musculus"))
        self.assertEqual(set(paper.get("GSM1")), set(fixtures.catalog().get("GSM1")))
        self.assertEqual(paper.to_records(), fixtures.catalog().to_records())
        self.assertEqual(paper.search("tumour").scores, fixtures.catalog().search("tumour").scores)
        self.assertEqual(paper.search(disease="breast cancer").gsms, ["GSM4"])  # raw v3 field, as before
        with self.assertRaises(UnknownFieldError) as caught:
            paper.search(organ="brain")
        self.assertIn("metadata v2", str(caught.exception))
        with self.assertRaises(UnknownFieldError):
            paper.search(min_confidence=0.6)
        self.assertNotIn("curated", paper.card("GSM1"))
        self.assertNotIn("metadata v2", paper.cite())

    def test_sha_mismatch_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError) as caught:
            self.open(sha256="0" * 64)
        self.assertIn("--release paper", caught.exception.hint)

    def test_table_missing_a_sample_or_moving_it_is_rejected(self) -> None:
        rows = fixtures.metadata_v2_rows()
        fixtures.write_metadata_v2(self.paths[V2_PATH], rows[:4])
        with self.assertRaisesRegex(IntegrityError, "lacks 1 samples"):
            self.open()
        rows[0]["published_gse"] = "GSE30"
        fixtures.write_metadata_v2(self.paths[V2_PATH], rows)
        with self.assertRaisesRegex(IntegrityError, "places GSM1"):
            self.open()

    def test_tables_without_sex_source_load_as_before(self) -> None:
        columns = tuple(c for c in fixtures.V2_COLUMNS if c != "sex_source")
        fixtures.write_metadata_v2(self.paths[V2_PATH], columns=columns)
        catalog = self.open()
        self.assertNotIn("sex_source", catalog.get("GSM2"))
        self.assertNotIn("sex_source", catalog.card("GSM2")["curated"]["donor"])
        self.assertEqual(catalog.get("GSM2")["sex"], "male")

    def test_reader_checks_columns_and_values(self) -> None:
        path = self.root / "bad.csv"
        path.write_text("gsm,organ\nGSM1,brain\n")
        with self.assertRaisesRegex(IntegrityError, "lacks metadata v2 columns"):
            read_metadata_v2(path)
        rows = fixtures.metadata_v2_rows()
        rows[0]["xenograft"] = "maybe"
        with self.assertRaisesRegex(IntegrityError, "xenograft"):
            read_metadata_v2(fixtures.write_metadata_v2(path, rows))


class FilterTests(Offline):
    def setUp(self) -> None:
        super().setUp()
        self.catalog = fixtures.catalog_v2(self.root)

    def ids(self, **kwargs: object) -> list[str]:
        return self.catalog.search(**kwargs).gsms

    def test_organ_sample_kind_life_stage_sex(self) -> None:
        self.assertEqual(self.ids(organ="brain"), ["GSM1", "GSM2"])
        self.assertEqual(self.ids(organ=["kidney", "breast"]), ["GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.ids(sample_kind="tumor"), ["GSM4"])  # alias of 'tumour tissue'
        self.assertEqual(self.ids(sample_kind="PDX"), ["GSM5"])
        self.assertEqual(self.ids(life_stage="aged"), ["GSM1", "GSM2"])
        self.assertEqual(self.ids(life_stage="elderly"), ["GSM1", "GSM2"])
        self.assertEqual(self.ids(sex="F"), ["GSM1"])
        self.assertEqual(self.ids(sex_source="sample text"), ["GSM2"])
        self.assertEqual(self.catalog.card("GSM2")["curated"]["donor"]["sex_source"], "sample text")
        self.assertIn("sex_source", {f["field"] for f in self.catalog.fields()})
        with self.assertRaises(UnknownValueError) as caught:
            self.catalog.search(organ="brian")  # controlled with v2: an error naming the closest value
        self.assertIn("brain", caught.exception.hint)

    def test_disease_by_mondo_id_name_and_substring(self) -> None:
        self.assertEqual(self.ids(disease="MONDO:0004975"), ["GSM1"])
        self.assertEqual(self.ids(disease="mondo_0007254"), ["GSM4", "GSM5"])
        self.assertEqual(self.ids(disease="Alzheimer disease"), ["GSM1"])
        self.assertEqual(self.ids(disease="carcinoma"), ["GSM5"])
        self.assertEqual(self.ids(disease=["kidney", "alzheimer"]), ["GSM1", "GSM3"])
        result = self.catalog.search(disease="alzheimers")
        self.assertEqual(result.gsms, [])
        self.assertTrue(any("alzheimer disease" in w for w in result.warnings))
        self.assertIn("disease=alzheimers", result.report["filters"])
        self.assertEqual(self.catalog.search(where="disease~alzheimer's").gsms, ["GSM1"])  # GEO text stays

    def test_disease_status_and_assay_fields(self) -> None:
        self.assertEqual(self.ids(disease_status="healthy"), ["GSM2"])
        self.assertEqual(self.ids(disease_status="disease"), ["GSM1", "GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.ids(assay_type="imaging"), ["GSM3"])
        self.assertEqual(self.ids(assay_name="visium cytassist"), ["GSM1"])
        self.assertEqual(self.ids(preservation="ffpe"), ["GSM1", "GSM4"])
        self.assertEqual(self.ids(preservation="frozen"), ["GSM2"])
        self.assertEqual(self.ids(platform="Visium"), ["GSM1", "GSM2", "GSM5"])  # corrected value
        self.assertEqual(self.ids(platform_v3="Visium HD"), ["GSM4", "GSM5"])

    def test_organism_by_name_common_name_taxon_and_any_host(self) -> None:
        self.assertEqual(self.ids(organism="house mouse"), ["GSM3", "GSM5"])
        self.assertEqual(self.ids(organism="mouse"), ["GSM3", "GSM5"])
        self.assertEqual(self.ids(organism="10090"), ["GSM3", "GSM5"])
        self.assertEqual(self.ids(organism="human"), ["GSM1", "GSM2", "GSM4", "GSM5"])
        with self.assertRaises(UnknownValueError) as caught:
            self.catalog.search(organism="hooman")
        self.assertIn("human", caught.exception.hint)

    def test_confidence_and_flags(self) -> None:
        self.assertEqual(self.ids(min_confidence=0.6), ["GSM1", "GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.ids(min_confidence=0.8), ["GSM1", "GSM3", "GSM5"])
        self.assertEqual(self.ids(exclude_flags=True), ["GSM1", "GSM3", "GSM4"])
        self.assertEqual(self.ids(exclude_flags="any"), ["GSM1", "GSM3", "GSM4"])
        self.assertEqual(self.ids(exclude_flags="feature_species_differs"), ["GSM1", "GSM2", "GSM3", "GSM4"])
        self.assertEqual(self.ids(exclude_flags=["normal_but_disease_text", "feature_species_differs"]),
                         ["GSM1", "GSM3", "GSM4"])
        with self.assertRaises(UnknownValueError):
            self.catalog.search(exclude_flags="feature_species_differ")
        with self.assertRaises(UnknownValueError):
            self.catalog.search(min_confidence=1.5)
        self.assertEqual(self.catalog.search(where="annotation_min_confidence<0.6").gsms, ["GSM2"])

    def test_null_confidence_means_hand_corrected_and_passes(self) -> None:
        import fixtures as f
        from sda_db.catalog import read_metadata_v2

        rows = f.metadata_v2_rows()
        # GSM2: disease_status was corrected by hand (null), the rest is confident; GSM4: every field by hand.
        rows[1]["annotation_confidence"] = json.dumps({"organ": 1.0, "sample_kind": 1.0, "disease_status": None,
                                                       "uberon": 0.95, "life_stage": 1.0})
        rows[1]["annotation_low_confidence"] = ""
        rows[3]["annotation_confidence"] = json.dumps({k: None for k in ("organ", "sample_kind", "disease_status",
                                                                         "uberon", "life_stage")})
        path = f.write_metadata_v2(self.root / "v221.csv", rows)
        catalog = f.catalog()
        catalog._attach_metadata_v2(read_metadata_v2(path), f.metadata_v2_info(path))
        self.assertEqual(catalog.get("GSM2")["annotation_min_confidence"], 0.95)
        self.assertIsNone(catalog.get("GSM4")["annotation_min_confidence"])
        self.assertEqual(catalog.search(min_confidence=0.6).gsms, ["GSM1", "GSM2", "GSM3", "GSM4", "GSM5"])
        self.assertEqual(catalog.search(min_confidence=0.96).gsms, ["GSM4"])  # others: uberon 0.95 is the model's
        self.assertIsNone(catalog.card("GSM2")["curated"]["annotation"]["confidence"]["disease_status"])
        rows[0]["annotation_confidence"] = ""  # no confidence recorded at all: not human-checked, fails
        path = f.write_metadata_v2(self.root / "none.csv", rows)
        catalog = f.catalog()
        catalog._attach_metadata_v2(read_metadata_v2(path), f.metadata_v2_info(path))
        self.assertNotIn("GSM1", catalog.search(min_confidence=0.6).gsms)

    def test_keywords_reach_v2_fields_and_aliases_still_work(self) -> None:
        self.assertEqual(self.catalog.search("hippocampal formation").gsms, ["GSM1"])  # UBERON name only
        self.assertEqual(set(self.catalog.search("ffpe").gsms), {"GSM1", "GSM4"})
        self.assertEqual(self.catalog.search("human breast carcinoma").gsms, ["GSM5"])
        self.assertEqual(self.catalog.search("cytassist").gsms, ["GSM1"])
        self.assertEqual(set(self.catalog.search("mouse kidney").gsms), {"GSM3"})
        self.assertEqual(self.catalog.search("hippo").gsms, ["GSM1", "GSM2"])  # literal fallback unchanged
        with self.assertRaises(UnknownValueError) as caught:
            self.catalog.search(platform="visum")
        self.assertIn("Visium", caught.exception.hint)
        self.assertNotIn("stated", self.catalog._index().vocabulary)  # 'not stated' is not indexed

    def test_facets_fields_card_and_compact(self) -> None:
        self.assertEqual({v["value"]: v["samples"] for v in self.catalog.values("organ")},
                         {"brain": 2, "breast": 2, "kidney": 1})
        self.assertEqual({v["value"] for v in self.catalog.values("disease_ontology_ids")},
                         {"MONDO:0004975", "MONDO:0002492", "MONDO:0007254", "MONDO:0006256", None})
        groups = self.catalog.summary(by=["organ", "disease_status"])["groups"]
        self.assertIn({"organ": "brain", "disease_status": "normal", "samples": 1, "studies": 1,
                       "observations": 4000, "with_image": 1}, groups)
        kinds = {f["field"]: f for f in self.catalog.fields()}
        self.assertEqual(kinds["organ"]["kind"], "category")
        self.assertEqual(kinds["annotation_min_confidence"]["kind"], "number")
        self.assertIn("platform_v3", kinds["platform"]["description"])
        card = self.catalog.card("GSM5")["curated"]
        self.assertEqual(card["species"]["organisms"], ["Homo sapiens", "Mus musculus"])
        self.assertEqual(card["annotation"]["metadata_flags"], ["feature_species_differs"])
        self.assertEqual(card["assay"]["platform_v3"], "Visium HD")
        compact = self.catalog.search(organ="brain").compact()["samples"][0]
        self.assertEqual((compact["organ"], compact["disease_ontology_names"]), ("brain", "Alzheimer disease"))
        self.assertNotIn("disease", compact)

    def test_cite_names_the_metadata_tag_and_v2_pubmed_ids(self) -> None:
        text = self.catalog.select(["GSM1"]).cite()
        self.assertIn("metadata-v2.2.1-20261106", text)
        self.assertIn("curated sample metadata v2", text)
        self.assertIn("PMID 111, 999", text)
        records = json.loads(self.catalog.select(["GSM1"]).cite(format="json"))
        self.assertEqual(records[2]["key"], "sdadb_metadata_v2_2_1_20261106")
        self.assertIn("SHA256 " + self.catalog.metadata_v2["sha256"], records[2]["note"])
        self.assertEqual(records[3]["pmids"], ["111", "999"])

    def test_selection_records_the_metadata(self) -> None:
        path = self.root / "cohort.json"
        self.catalog.search(organ="brain").save_selection(path)
        record = json.loads(path.read_text())
        self.assertEqual(record["metadata"]["tag"], "metadata-v2.2.1-20261106")
        self.assertEqual(record["samples"][0]["organ"], "brain")


class InterfaceTests(Offline):
    def setUp(self) -> None:
        super().setUp()
        catalog = fixtures.catalog_v2(self.root)
        opener = patch("sda_db.catalog.Catalog.open", side_effect=lambda *a, **k: catalog)
        opener.start()
        self.addCleanup(opener.stop)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def test_cli_flags(self) -> None:
        def ids(*argv: str) -> list[str]:
            status, out, err = self.run_cli("search", *argv, "--format", "ids")
            self.assertEqual(status, 0, err)
            return out.split()

        self.assertEqual(ids("--organ", "breast", "--disease", "carcinoma"), ["GSM5"])
        self.assertEqual(ids("--disease", "MONDO:0004975"), ["GSM1"])
        self.assertEqual(ids("--disease-status", "normal"), ["GSM2"])
        self.assertEqual(ids("--sample-kind", "tumour tissue", "--sample-kind", "xenograft"), ["GSM4", "GSM5"])
        self.assertEqual(ids("--life-stage", "adult"), ["GSM3"])
        self.assertEqual(ids("--sex", "male"), ["GSM2"])
        self.assertEqual(ids("--organism", "10090", "--assay-type", "imaging-based"), ["GSM3"])
        self.assertEqual(ids("--assay-name", "Visium HD", "--preservation", "FFPE"), ["GSM4"])
        self.assertEqual(ids("--organ", "brain", "--min-confidence", "0.6"), ["GSM1"])
        self.assertEqual(ids("--exclude-flags"), ["GSM1", "GSM3", "GSM4"])
        self.assertEqual(ids("--exclude-flags", "normal_but_disease_text"), ["GSM1", "GSM3", "GSM4", "GSM5"])
        status, out, _ = self.run_cli("search", "--organ", "brain", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["metadata"], "metadata-v2.2.1-20261106")
        self.assertIn("organ=brain", payload["filters"])
        status, _, err = self.run_cli("search", "--organ", "brian")
        self.assertNotEqual(status, 0)
        self.assertIn("brain", err)
        status, out, _ = self.run_cli("values", "organ", "--disease-status", "disease", "--json")
        self.assertEqual({v["value"] for v in json.loads(out)["values"]}, {"brain", "kidney", "breast"})

    def test_cli_cite_names_the_metadata_tag(self) -> None:
        status, out, _ = self.run_cli("cite", "GSM1")
        self.assertEqual(status, 0)
        self.assertIn("metadata-v2.2.1-20261106", out)

    def test_mcp_tools_accept_v2_filters(self) -> None:
        server = Server(download_dir=self.root / "mcp")
        self.assertEqual(len(server.tools), 13)
        props = server.tools["search_samples"][0]["inputSchema"]["properties"]
        for name in ("organ", "disease", "disease_status", "sample_kind", "life_stage", "sex", "assay_type",
                     "assay_name", "preservation", "min_confidence", "exclude_flags"):
            self.assertIn(name, props)
            self.assertIn(name, server.tools["field_values"][0]["inputSchema"]["properties"])
        result = server.call_tool("search_samples", {"organ": "breast|kidney", "disease": "cancer",
                                                     "min_confidence": 0.6})
        self.assertFalse(result["isError"], result)
        content = result["structuredContent"]
        self.assertEqual([s["gsm"] for s in content["samples"]], ["GSM4", "GSM5"])
        self.assertEqual(content["metadata"], "metadata-v2.2.1-20261106")
        result = server.call_tool("search_samples", {"exclude_flags": "any", "organism": "human"})
        self.assertEqual(result["structuredContent"]["total"], 2)
        result = server.call_tool("search_samples", {"min_confidence": True})
        self.assertTrue(result["isError"])
        result = server.call_tool("summarize", {"by": "organ", "sample_kind": "primary tissue"})
        self.assertEqual({g["organ"] for g in result["structuredContent"]["groups"]}, {"brain", "kidney"})


class PaperInterfaceTests(Offline):
    """CLI and MCP on a release without metadata v2 keep the client 0.3 behaviour."""

    def setUp(self) -> None:
        super().setUp()
        opener = patch("sda_db.catalog.Catalog.open", side_effect=lambda *a, **k: fixtures.catalog())
        opener.start()
        self.addCleanup(opener.stop)

    def test_disease_flag_is_a_text_substring_and_v2_flags_explain_themselves(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["search", "--disease", "CANCER", "--format", "ids", "--release", "paper"]), 0)
        self.assertEqual(out.getvalue().split(), ["GSM4"])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(["search", "--organ", "brain", "--release", "paper"])
        self.assertNotEqual(status, 0)
        self.assertIn("metadata v2", err.getvalue())
        server = Server(download_dir=self.root / "mcp")
        result = server.call_tool("search_samples", {"disease": "kidney", "release": "paper"})
        self.assertEqual([s["gsm"] for s in result["structuredContent"]["samples"]], ["GSM3"])
        self.assertNotIn("metadata", result["structuredContent"])


if __name__ == "__main__":
    unittest.main()
