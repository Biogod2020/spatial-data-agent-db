"""CLI contracts (JSON envelopes, exit codes, piping) and the MCP stdio protocol."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from huggingface_hub import errors as hub_errors

from sda_db.cli import main
from sda_db.mcp_server import PROTOCOL_VERSIONS, Server, serve

import fixtures


def gated(*args: object, **kwargs: object) -> str:
    raise hub_errors.GatedRepoError("gated", response=httpx.Response(403, request=httpx.Request("GET", "https://x")))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        for target in (patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only")),
                       patch("sda_db.catalog.Catalog.open", side_effect=lambda *a, **k: fixtures.catalog()),
                       patch("huggingface_hub.hf_hub_download", side_effect=gated)):
            target.start()
            self.addCleanup(target.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def test_search_json_is_compact_and_complete(self) -> None:
        status, out, _ = self.run_cli("search", "alzheimer", "--limit", "1")
        self.assertEqual(status, 0)
        payload = json.loads(out)
        self.assertNotIn("\n", out.strip())
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["shown"], 1)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["samples"][0]["gsm"], "GSM1")
        self.assertIn("GSE10", payload["studies"])
        self.assertEqual(payload["term_matches"], {"alzheimer": 2})
        self.assertTrue(payload["next"])

    def test_filters_flags_and_ids_format(self) -> None:
        status, out, _ = self.run_cli("search", "--platform", "visium hd", "--tissue", "TUMOUR", "--format", "ids")
        self.assertEqual((status, out.split()), (0, ["GSM4"]))
        status, out, _ = self.run_cli("search", "--organism", "mouse", "--has-image", "--fields", "gsm,n_obs")
        self.assertEqual(json.loads(out)["samples"], [{"gsm": "GSM3", "n_obs": 200000}])
        status, out, _ = self.run_cli("search", "--studies", "--split", "val")
        self.assertEqual(json.loads(out)["studies"][0]["published_gse"], "GSE30")

    def test_exclusion_words_are_not_mistaken_for_options(self) -> None:
        status, out, _ = self.run_cli("search", "tumour", "-xeno*", "--format", "ids")
        self.assertEqual((status, out.split()), (0, ["GSM4"]))
        status, out, _ = self.run_cli("values", "platform", "-q", "tumour", "-xenograft")
        self.assertEqual(json.loads(out)["selection_samples"], 1)
        with self.assertRaises(SystemExit):
            self.run_cli("search", "tumour", "--bogus-option")

    def test_gene_flags_and_features_command(self) -> None:
        index = fixtures.feature_index_file(self.root)
        status, out, _ = self.run_cli("search", "--gene", "GFAP", "--gene", "aqp4", "--feature-index", str(index))
        payload = json.loads(out)
        self.assertEqual([r["gsm"] for r in payload["samples"]], ["GSM1", "GSM2"])
        self.assertIn("genes=GFAP|aqp4", payload["filters"])
        status, out, _ = self.run_cli("features", "GSM4", "--contains", "cd3", "--feature-index", str(index))
        self.assertEqual(json.loads(out)["rows"][0]["feature_name"], "CD3E_protein")
        status, out, _ = self.run_cli("features", "GSM5", "--feature-index", str(index))
        self.assertIn("Whole-transcriptome", json.loads(out)["note"])

    def test_unknown_value_exits_2_with_structured_error(self) -> None:
        status, out, err = self.run_cli("search", "--platform", "visum")
        self.assertEqual((status, out), (2, ""))
        error = json.loads(err)["error"]
        self.assertEqual(error["code"], "unknown_value")
        self.assertIn("Visium", error["hint"])

    def test_values_summary_show_fields_releases(self) -> None:
        self.assertEqual(json.loads(self.run_cli("values", "platform", "--top", "1")[1])["values"][0]["value"],
                         "Visium")
        summary = json.loads(self.run_cli("summary", "--by", "split")[1])
        self.assertEqual(summary["totals"]["samples"], 5)
        items = json.loads(self.run_cli("show", "GSM1", "GSE10")[1])["items"]
        self.assertEqual([i["type"] for i in items], ["sample", "study"])
        self.assertTrue(json.loads(self.run_cli("fields")[1])["fields"])
        releases = json.loads(self.run_cli("releases")[1])["releases"]
        self.assertTrue(releases[0]["default"])

    def test_export_then_pipe_ids_into_a_dry_run(self) -> None:
        cohort = self.root / "cohort.json"
        self.assertEqual(self.run_cli("search", "kidney", "--output", str(cohort))[0], 0)
        status, out, err = self.run_cli("download", "--from", str(cohort), "--dry-run", "-d", str(self.root / "d"))
        plan = json.loads(out)
        self.assertEqual((status, plan["samples"], plan["total_bytes"]), (0, 1, None))
        self.assertTrue(any("access" in n for n in plan["notes"]))
        ids = self.root / "ids.txt"
        ids.write_text("GSE30\n")
        self.assertEqual(json.loads(self.run_cli("download", "--from", str(ids), "--dry-run")[1])["samples"], 2)
        ids.write_text("GSM1 (from GSE30)\n")  # GSMs win over series mentioned in free text
        status, out, err = self.run_cli("download", "--from", str(ids), "--dry-run")
        self.assertEqual(json.loads(out)["samples"], 1)
        self.assertIn("GSMs only", err)

    def test_from_csv_and_search_json_use_only_the_gsm_column(self) -> None:
        table = self.root / "cohort.csv"
        self.run_cli("search", "--gse", "GSE10", "--output", str(table))  # GSM2 has aliases GSM9;GSM8
        status, out, _ = self.run_cli("download", "--from", str(table), "--dry-run")
        self.assertEqual((status, json.loads(out)["samples"]), (0, 2))
        piped = self.root / "search.json"
        piped.write_text(self.run_cli("search", "kidney")[1])
        status, out, _ = self.run_cli("cite", "--from", str(piped), "--style", "json")
        self.assertEqual(status, 0)
        self.assertEqual([r.get("gse") for r in json.loads(out)][2:], ["GSE20", "GSE21"])
        lines = self.root / "rows.jsonl"
        self.run_cli("search", "tumour", "--output", str(lines))
        self.assertEqual(json.loads(self.run_cli("download", "--from", str(lines), "--dry-run")[1])["samples"], 2)

    def test_machine_formats_print_every_match(self) -> None:
        with patch("sda_db.cli.TABLE_FIELDS", ("gsm",)):
            status, out, err = self.run_cli("search", "--format", "ids")
        self.assertEqual(len(out.split()), 5)
        status, out, err = self.run_cli("search", "--format", "ids", "--limit", "2")
        self.assertEqual(len(out.split()), 2)
        self.assertIn("showing 2 of 5", err)
        status, out, err = self.run_cli("summary", "--by", "split", "--format", "csv")
        self.assertTrue(out.startswith("split,"))

    def test_size_guard_fails_closed_on_unknown_sizes(self) -> None:
        from sda_db.batch import DownloadPlan, SamplePlan

        plan = DownloadPlan("r", "c" * 40, str(self.root), False, [SamplePlan("GSM1", "samples/GSE10/GSM1",
                                                                               {"spatial.h5ad": None})], "x")
        with patch("sda_db.batch.plan_download", return_value=plan), patch("sda_db.batch.download") as fetch:
            status, _, err = self.run_cli("download", "GSM1")
            self.assertEqual((status, json.loads(err)["error"]["code"]), (7, "size_limit"))
            fetch.assert_not_called()
            self.run_cli("download", "GSM1", "--yes")
            fetch.assert_called_once()

    def test_download_needs_a_selection_and_reports_access(self) -> None:
        status, _, err = self.run_cli("download")
        self.assertEqual(status, 3)
        self.assertEqual(json.loads(err)["error"]["code"], "not_found")
        status, _, err = self.run_cli("download", "GSM1", "-d", str(self.root / "d"))
        self.assertEqual(status, 4)
        self.assertIn("Request access", json.loads(err)["error"]["hint"])

    def test_cite_to_file(self) -> None:
        target = self.root / "refs.bib"
        status, _, err = self.run_cli("cite", "GSM3", "-o", str(target))
        self.assertEqual(status, 0)
        self.assertIn("@misc{geo_GSE20", target.read_text())
        self.assertIn("2 GEO series", err)


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        for target in (patch.object(socket.socket, "connect", side_effect=AssertionError("Offline tests only")),
                       patch("huggingface_hub.hf_hub_download", side_effect=gated)):
            target.start()
            self.addCleanup(target.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.server = Server(download_dir=Path(folder.name), max_download_bytes=10)
        self.server._catalogs[None] = fixtures.catalog()

    def request(self, method: str, params: dict | None = None, identifier: int = 1) -> dict:
        return self.server.handle({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}})

    def call(self, name: str, **arguments: object) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})["result"]

    def test_initialize_negotiates_versions_and_sends_instructions(self) -> None:
        result = self.request("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("plan_download", result["instructions"])
        self.assertEqual(self.request("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"],
                         PROTOCOL_VERSIONS[0])

    def test_tool_schemas_are_valid_objects(self) -> None:
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 13)
        for tool in tools:
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertTrue(set(schema.get("required", [])) <= set(schema["properties"]), tool["name"])
            self.assertTrue(tool["description"])
            json.dumps(tool)

    def test_search_values_and_errors_as_results(self) -> None:
        result = self.call("search_samples", query="tumour", limit=1)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["total"], 2)
        self.assertEqual(json.loads(result["content"][0]["text"])["shown"], 1)
        self.assertEqual(self.call("search_samples", platform="Visium|Xenium")["structuredContent"]["total"], 3)
        self.assertEqual(self.call("field_values", field="split")["structuredContent"]["distinct"], 3)
        failed = self.call("search_samples", platform="Visum")
        self.assertTrue(failed["isError"])
        self.assertEqual(failed["structuredContent"]["error"]["code"], "unknown_value")
        self.assertTrue(self.call("search_samples", nonsense=1)["isError"])

    def test_arguments_are_type_checked(self) -> None:
        refused = self.call("download_samples", gsms="GSM1", confirm="false")
        self.assertTrue(refused["isError"])
        self.assertIn("boolean", refused["structuredContent"]["error"]["message"])
        self.assertTrue(self.call("search_samples", has_image="false")["isError"])
        self.assertTrue(self.call("search_samples", min_obs="900")["isError"])
        self.assertTrue(self.call("search_samples", limit=True)["isError"])
        self.assertTrue(self.call("field_values")["isError"])  # required argument
        self.assertEqual(self.request("tools/call", ["search_samples"])["error"]["code"], -32602)
        self.assertEqual(self.server.handle([])["error"]["code"], -32600)

    def test_gene_arguments_and_feature_tool(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            index = fixtures.feature_index_file(Path(folder))
            with patch.dict("os.environ", {"SDA_DB_FEATURE_INDEX": str(index)}):
                found = self.call("search_samples", genes="GFAP, Snap25", gene_match="any")["structuredContent"]
                self.assertEqual([r["gsm"] for r in found["samples"]], ["GSM1", "GSM2", "GSM3", "GSM5"])
                listed = self.call("list_features", gsm="GSM3", contains="snap")["structuredContent"]
                self.assertEqual(listed["rows"][0]["source_feature_id"], "ENSMUSG00000027273")

    def test_size_cap_accepts_zero_and_unit_strings(self) -> None:
        self.assertEqual(Server(max_download_bytes=0).max_download_bytes, 0)
        with patch.dict("os.environ", {"SDA_DB_MAX_DOWNLOAD_BYTES": "50GB"}):
            self.assertEqual(Server().max_download_bytes, 50 * 10**9)

    def test_utf8_output_on_a_non_utf8_console(self) -> None:
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp1252")
        stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        with patch("sys.stdout", console), patch("sda_db.mcp_server.INSTRUCTIONS", "β-amyloid"):
            serve(stdin=stdin)
        console.flush()
        self.assertIn("β-amyloid", json.loads(raw.getvalue().decode("utf-8"))["result"]["instructions"])

    def test_download_tool_plans_first_and_needs_a_selection(self) -> None:
        planned = self.call("download_samples", gsms="GSM1")["structuredContent"]
        self.assertEqual(planned["status"], "planned")
        refused = self.call("download_samples", gsms="GSM1", confirm=True)["structuredContent"]
        self.assertEqual(refused["error"]["code"], "size_limit")  # sizes unknown without access
        self.assertEqual(self.call("download_samples")["structuredContent"]["error"]["code"], "not_found")

    def test_cards_studies_cite_and_releases(self) -> None:
        self.assertEqual(self.call("get_sample", gsm="GSM3")["structuredContent"]["assay"]["platform"], "Xenium")
        self.assertEqual(self.call("get_study", gse="GSE10")["structuredContent"]["samples"], 2)
        self.assertIn("@misc{geo_GSE30", self.call("cite", gse="GSE30")["structuredContent"]["citations"])
        self.assertTrue(self.call("list_releases")["structuredContent"]["releases"])

    def test_protocol_errors_notifications_and_batches(self) -> None:
        self.assertEqual(self.request("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(self.request("bogus/method")["error"]["code"], -32601)
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        batch = self.server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual(batch, [{"jsonrpc": "2.0", "id": 1, "result": {}}])
        self.assertEqual(self.request("resources/list")["result"], {"resources": []})

    def test_serve_loop_over_stdio(self) -> None:
        lines = io.StringIO("\n".join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                   "params": {"protocolVersion": "2025-03-26"}}),
                                       "{not json", "",
                                       json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]) + "\n")
        sink = io.StringIO()
        with patch("sda_db.mcp_server.Server._register"):
            serve(stdin=lines, stdout=sink)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in responses], [1, None, 2])
        self.assertEqual(responses[1]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
