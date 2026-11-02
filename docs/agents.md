# AI agents and MCP

SpatialDataAgent-DB is built to be used by agents as well as people. There are three ways in, from most to
least integrated:

1. **MCP server** (`sda-db mcp`): typed tools for any MCP client.
2. **JSON CLI**: any agent that can run shell commands gets compact JSON, stable error codes and hints.
3. **Agent skill** ([`skills/sda-db/SKILL.md`](../skills/sda-db/SKILL.md)): a playbook that teaches an agent
   the workflow and the data pitfalls.

## Register the MCP server

Install the package first (see the [README](../README.md#install)). The server is part of the core install.

**Claude Code**

```bash
claude mcp add sda-db -- sda-db mcp
# or, without installing anything:
claude mcp add sda-db -- uvx --from "git+https://github.com/Biogod2020/spatial-data-agent-db" sda-db mcp
```

Or commit `.mcp.json` to a project:

```json
{"mcpServers": {"sda-db": {"command": "sda-db", "args": ["mcp"],
                           "env": {"SDA_DB_DOWNLOAD_DIR": "/data/sda-db", "SDA_DB_MAX_DOWNLOAD_BYTES": "50000000000"}}}}
```

**Claude Desktop** (`claude_desktop_config.json`), **Cursor** (`.cursor/mcp.json`) and **Gemini CLI**
(`~/.gemini/settings.json`) use the same `mcpServers` block.

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.sda-db]
command = "sda-db"
args = ["mcp"]
env = { SDA_DB_DOWNLOAD_DIR = "/data/sda-db" }
```

**VS Code** (`.vscode/mcp.json`):

```json
{"servers": {"sda-db": {"type": "stdio", "command": "sda-db", "args": ["mcp"]}}}
```

If the app cannot find `sda-db`, give the absolute path from `which sda-db`.

## Tools

| Tool | What it returns |
|---|---|
| `search_samples` | Ranked samples (or studies, with `group_by_study`) for keywords, filters and `genes`; `total`, compact rows, study titles, warnings |
| `list_fields` | Every field with its meaning, kind, distinct count and top values, plus the filter syntax |
| `field_values` | Distinct values of one field with sample/study/observation counts, optionally within a selection |
| `summarize` | Totals, optionally cross-tabulated by fields |
| `get_sample` | The full sample card; with `include_sample_json` also its `sample.json` (needs access) |
| `get_study` | Title, summary, design, PubMed IDs and the samples of a GEO series |
| `list_features` | Genes/features measured in one sample (from the feature index; no download), filterable by text |
| `plan_download` | Bytes per sample and in total, local files, free space, whether it fits the limit |
| `download_samples` | Without `confirm=true`, the plan; with it, a verified download into the server directory |
| `inspect_sample` | Shape, feature types, columns, coordinate range, QC and image levels of a downloaded sample |
| `cite` | BibTeX/text/JSON for the paper, the release and every GEO series of a selection |
| `list_releases` | Known releases and the default |
| `check_access` | The `doctor` checks: token, login, dataset access, public catalog, disk |

The selection filters are the same as in the CLI. Alternatives inside one argument are joined with `|`, as
in `platform: "Visium|Visium HD"`. Every result names its `release` and `commit`.

## Safety limits

- **Bounded results.** `search_samples` returns at most 200 rows. `total` and `truncated` say when there are
  more, and `group_by_study` summarizes large selections.
- **Plan first.** `download_samples` returns only a plan unless `confirm=true`.
- **Size cap.** Downloads above the cap are refused. The default cap is 20 GB per call; set it with
  `--max-download-size` or `SDA_DB_MAX_DOWNLOAD_BYTES` (e.g. `50GB`; `0` blocks every transfer). The cap
  applies per call, not in total. If sizes are unknown, the download is refused rather than guessed.
- **Strict arguments.** Arguments are type-checked against each tool's schema, so `"confirm": "false"` is an
  error and does not count as true.
- **Fixed location.** Files go only to `--download-dir` or `SDA_DB_DOWNLOAD_DIR`, or by default to
  `~/.cache/sda-db/downloads`. An agent cannot choose another path.
- **Read-only option.** `--no-allow-downloads` removes the download tool entirely.
- **No credentials.** The server never reads, prints or stores tokens. It uses the standard Hugging Face
  login of the user running it.
- **Unknown arguments.** Arguments a tool does not define are rejected, with the list of allowed ones.

## The JSON CLI for agents

Agents that run shell commands do not need MCP:

```bash
sda-db search alzheimer --organism human --has-image --limit 10      # piped → compact JSON
sda-db values disease --organism human --top 40
sda-db download --from cohort.json --dry-run
```

Errors go to stderr as `{"error": {"code", "message", "hint"}}`, with the [exit codes](cli.md#conventions).
A misspelled value returns the closest valid values, so an agent can correct itself in one step.

## Install the agent skill

The skill tells an agent when to use the database, the search → inspect → plan → download → read workflow,
and how to interpret units, counts, images and splits.

```bash
mkdir -p ~/.claude/skills && cp -r skills/sda-db ~/.claude/skills/      # Claude Code (user scope)
```

Other agents can read [`skills/sda-db/SKILL.md`](../skills/sda-db/SKILL.md) or [`llms.txt`](../llms.txt)
directly.
