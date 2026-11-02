"""Search, filters, facets, studies, cards, exports and citations on a synthetic catalog."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from sda_db import NotFoundError, UnknownFieldError, UnknownValueError
from sda_db.query import parse_where, tokenize

from fixtures import catalog


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        self.catalog = catalog()

    def test_keywords_match_study_text_and_rank_sample_fields_first(self) -> None:
        result = self.catalog.search("alzheimer hippocampus")
        self.assertEqual(result.gsms[0], "GSM1")  # tissue matches as well as the study title
        self.assertEqual(set(result.gsms), {"GSM1", "GSM2"})
        self.assertGreater(result.scores["GSM1"], result.scores["GSM2"])

    def test_all_terms_required_unless_any(self) -> None:
        self.assertEqual(self.catalog.search("kidney hippocampus").gsms, [])
        self.assertEqual(set(self.catalog.search("kidney hippocampus", match="any").gsms), {"GSM1", "GSM2", "GSM3"})

    def test_phrase_exclusion_prefix_and_ignored_terms(self) -> None:
        self.assertEqual(self.catalog.search('"frontal cortex"').gsms, ["GSM2"])
        self.assertNotIn("GSM5", self.catalog.search("tumour -xenograft").gsms)
        self.assertIn("GSM3", self.catalog.search("fibro*").gsms)
        result = self.catalog.search("kidney zzqx")
        self.assertEqual(result.gsms, ["GSM3"])
        self.assertTrue(any("zzqx" in w for w in result.warnings))

    def test_negated_prefixes_and_phrases_exclude(self) -> None:
        self.assertEqual(self.catalog.search("tumour -xeno*").gsms, ["GSM4"])
        self.assertNotIn("GSM2", self.catalog.search('human -"frontal cortex"').gsms)
        self.assertEqual(self.catalog.search("-mouse").gsms, ["GSM1", "GSM2", "GSM4"])  # exclusion only
        self.assertIsNone(self.catalog.search("-mouse").scores)

    def test_unmatched_keywords_fall_back_to_a_literal_substring(self) -> None:
        result = self.catalog.search("hippo")  # client 0.2 semantics for partial words
        self.assertEqual(result.gsms, ["GSM1", "GSM2"])
        self.assertTrue(any("literal substring" in w for w in result.warnings))
        self.assertEqual(self.catalog.search("zzqx").gsms, [])

    def test_superseries_study_aggregates_every_member(self) -> None:
        from sda_db import Catalog

        import fixtures

        rows = [fixtures.row("GSM11", "GSE1+GSE9"), fixtures.row("GSM12", "GSE2+GSE9"),
                fixtures.row("GSM13", "GSE2+GSE9")]
        catalog = Catalog(rows)
        catalog._attach_metadata([{"gse": "GSE9", "title": "Super"}, {"gse": "GSE2", "title": "Sub2"}], [])
        study = catalog.study("GSE9")
        self.assertEqual((study["samples"], study["title"], study["published_gse"]), (3, "Super", ["GSE1", "GSE2"]))
        self.assertEqual(len(study["member_studies"]), 2)

    def test_descending_sort_keeps_tie_order_and_booleans_facet_as_booleans(self) -> None:
        self.assertEqual(self.catalog.search(sort="-has_image").gsms, ["GSM1", "GSM2", "GSM3", "GSM4", "GSM5"])
        self.assertEqual({v["value"] for v in self.catalog.values("has_image")}, {True, False})
        groups = self.catalog.summary(by="has_image")["groups"]
        self.assertEqual({g["has_image"]: g["samples"] for g in groups}, {True: 3, False: 2})

    def test_only_generic_words_apply_no_keyword_filter(self) -> None:
        result = self.catalog.search("spatial data")
        self.assertEqual(len(result), 5)
        self.assertIsNone(result.scores)
        self.assertTrue(result.warnings)

    def test_tokenizer_keeps_h_and_e_and_stems_plurals(self) -> None:
        self.assertEqual(tokenize("H&E tumors Alzheimer's"), ["h&e", "tumor", "alzheimer"])
        self.assertEqual(set(self.catalog.search("HE").gsms), {"GSM1", "GSM2"})

    def test_aliases_for_organism_platform_and_stain(self) -> None:
        self.assertEqual(self.catalog.search(organism="mouse").gsms, ["GSM3", "GSM5"])  # multi-species member
        self.assertEqual(self.catalog.search(platform="visium-hd").gsms, ["GSM4", "GSM5"])
        self.assertEqual(self.catalog.search(platform=["Xenium", "visium hd"]).gsms, ["GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.catalog.search(image_stain="he").gsms, ["GSM1", "GSM2"])
        self.assertEqual(len(self.catalog.search(organism="humans")), 4)

    def test_unknown_controlled_value_raises_with_suggestion(self) -> None:
        with self.assertRaises(UnknownValueError) as caught:
            self.catalog.search(platform="Visum")
        self.assertIn("Visium", caught.exception.hint)
        with self.assertRaisesRegex(UnknownFieldError, "Unsupported") as field:
            self.catalog.search(platfrom="Visium")
        self.assertIn("platform", field.exception.hint)

    def test_free_text_exact_miss_warns_instead_of_raising(self) -> None:
        result = self.catalog.search(tissue="brain")
        self.assertEqual(len(result), 0)
        self.assertTrue(any("tissue~brain" in w for w in result.warnings))

    def test_where_operators(self) -> None:
        self.assertEqual(self.catalog.search(where="n_obs>=90000").gsms, ["GSM3", "GSM4"])
        self.assertEqual(self.catalog.search(where=["tissue~CORTEX"]).gsms, ["GSM2"])
        self.assertEqual(self.catalog.search(where="platform!=Visium").gsms, ["GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.catalog.search(where="disease=").gsms, ["GSM2", "GSM5"])
        self.assertEqual(self.catalog.search(where="disease!=").gsms, ["GSM1", "GSM3", "GSM4"])
        self.assertEqual(self.catalog.search(where="split=val|test").gsms, ["GSM3", "GSM4", "GSM5"])
        self.assertEqual(self.catalog.search(where="flags=attested").gsms, ["GSM3"])
        self.assertEqual(self.catalog.search(where="has_image=yes", gse="GSE21").gsms, ["GSM3"])
        with self.assertRaises(UnknownValueError):
            self.catalog.search(where="n_obs>=many")
        with self.assertRaises(UnknownFieldError):
            parse_where("no operator here")

    def test_parse_where_prefers_earliest_longest_operator(self) -> None:
        self.assertEqual(parse_where("n_obs>=5").op, ">=")
        self.assertEqual(parse_where("tissue!~brain").op, "!~")
        self.assertEqual(parse_where("review_notes~a=b").values, ("a=b",))

    def test_python_booleans_required_for_keyword_filters(self) -> None:
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.catalog.search(has_image="false")

    def test_sort_and_chained_selections_keep_release(self) -> None:
        result = self.catalog.search(sort="-n_obs")
        self.assertEqual(result.gsms[:2], ["GSM3", "GSM4"])
        chained = result.search(platform="Visium HD").head(1)
        self.assertEqual(chained.gsms, ["GSM4"])
        self.assertEqual(chained.revision, self.catalog.revision)
        self.assertIn("GSE30", chained.studies)

    def test_select_preserves_order_and_names_missing(self) -> None:
        self.assertEqual(self.catalog.select(["GSM4", "GSM1", "GSM4"]).gsms, ["GSM4", "GSM1"])
        with self.assertRaises(NotFoundError) as caught:
            self.catalog.select(["GSM9"])
        self.assertIn("alias of GSM2", caught.exception.hint)
        with self.assertRaisesRegex(ValueError, "absent"):
            self.catalog.select(["GSM98", "GSM99"])

    def test_values_summary_and_studies(self) -> None:
        values = {v["value"]: v for v in self.catalog.values("platform")}
        self.assertEqual(values["Visium HD"]["samples"], 2)
        self.assertEqual(values["Visium"]["studies"], 1)
        organisms = {v["value"]: v["samples"] for v in self.catalog.values("organism")}
        self.assertEqual(organisms["Mus musculus"], 2)
        self.assertIn(None, [v["value"] for v in self.catalog.values("disease")])  # missing is a value
        summary = self.catalog.summary(by=["platform", "split"])
        self.assertEqual(summary["totals"]["samples"], 5)
        self.assertEqual(summary["totals"]["studies"], 3)
        self.assertEqual(sum(g["samples"] for g in summary["groups"]), 5)
        studies = self.catalog.list_studies()
        self.assertEqual(studies[0]["published_gse"], "GSE10")
        self.assertEqual(studies[0]["pubmed_ids"], ["111"])
        study = self.catalog.study("GSE21")  # secondary membership
        self.assertEqual(study["samples"], 1)
        with self.assertRaises(NotFoundError):
            self.catalog.study("GSE99")

    def test_fields_describe_kinds_and_metadata(self) -> None:
        fields = {f["field"]: f for f in self.catalog.fields()}
        self.assertEqual(fields["platform"]["kind"], "category")
        self.assertEqual(fields["n_obs"]["kind"], "number")
        self.assertEqual(fields["has_image"]["kind"], "boolean")
        self.assertIn("GEO", fields["disease"]["description"])
        self.assertEqual(fields["study_title"]["missing"], 0)

    def test_card_groups_everything_for_one_sample(self) -> None:
        card = self.catalog.card("GSM1")
        self.assertEqual(card["biology"]["disease"], "Alzheimer's disease")
        self.assertEqual(card["study"]["pubmed_ids"], ["111"])
        self.assertTrue(card["files"]["image.tif"].endswith("GSM1/image.tif"))
        self.assertIsNone(self.catalog.card("GSM4")["files"]["image.tif"])

    def test_compact_rows_drop_empty_fields_and_list_titles_once(self) -> None:
        compact = self.catalog.search("tumour").compact(5)
        self.assertNotIn("study_title", compact["samples"][0])
        self.assertNotIn("image_stain", compact["samples"][0])
        self.assertIn("score", compact["samples"][0])
        self.assertEqual(list(compact["studies"]), ["GSE30"])

    def test_exports_and_selection_replay(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            selection = self.catalog.search(platform="Visium HD")
            selection.export(root / "s.csv")
            selection.export(root / "s.jsonl", fields="summary")
            selection.export(root / "s.json")
            self.assertEqual((root / "s.csv").read_text().count("\n"), 4)  # header + 2 rows (one multi-line)
            self.assertEqual(len((root / "s.jsonl").read_text().splitlines()), 2)
            record = json.loads((root / "s.json").read_text())
            self.assertEqual(record["schema"], "sda-db-selection-2")
            self.assertEqual(record["filters"], ["platform=Visium HD"])
            with patch("sda_db.catalog.Catalog.open", return_value=self.catalog) as opened:
                replay = type(self.catalog).from_selection(root / "s.json")
            opened.assert_called_once()
            self.assertEqual(replay.gsms, ["GSM4", "GSM5"])
            with self.assertRaises(ValueError):
                selection.export(root / "s.xlsx")

    def test_cite_bibtex_escapes_and_lists_every_series(self) -> None:
        from sda_db.cite import _escape

        self.assertEqual(_escape("A & B_1 {x} 50%"), r"A \& B\_1 \{x\} 50\%")
        bibtex = self.catalog.search(platform=["Visium HD", "Xenium"]).cite()
        self.assertIn("@article{ji2026spatialdataagent", bibtex)
        self.assertIn("@misc{geo_GSE21", bibtex)  # secondary series membership is cited too
        self.assertIn("PMID 333", bibtex)
        self.assertIn("doi          = {10.1/abc}", bibtex)
        records = json.loads(self.catalog.select(["GSM1"]).cite(format="json"))
        self.assertEqual([r["key"] for r in records][-1], "geo_GSE10")
        self.assertIn("1 sample in", records[-1]["note"])
        self.assertIn("GSE10:", self.catalog.select(["GSM1"]).cite(format="text"))

    def test_pandas_frame(self) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError:
            self.skipTest("pandas not installed")
        frame = self.catalog.search("tumour").to_pandas()
        self.assertEqual(list(frame.index), ["GSM4", "GSM5"])
        self.assertIn("score", frame)


if __name__ == "__main__":
    unittest.main()
