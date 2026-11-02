# Notes for AI agents

## Using the database

You can work with SpatialDataAgent-DB in three ways:

- **CLI:** `sda-db`. It prints compact JSON when piped.
- **MCP server:** `sda-db mcp`, with 13 tools.
- **Python:** `import sda_db`.

Read [`skills/sda-db/SKILL.md`](skills/sda-db/SKILL.md) first. It gives the workflow (search → values/show →
plan → download → load → cite) and the data pitfalls: count state, coordinate units, observation units,
heuristic stains and leakage-safe splits.

Work by these rules:

- Plan before you download. The paper release is about 1.8 TB, and images are opt-in.
- Report the release name and commit with every selection.
- Never ask for, print or pass Hugging Face tokens. Use `sda-db doctor` to diagnose access.

## Working on this repository

- Layout: `src/sda_db/` (package), `tests/` (offline unittest), `docs/`, `examples/`, `skills/`.
- Run the tests with `python -m unittest discover -s tests`. They must stay offline: they patch
  `socket.connect` and use synthetic payloads. `tests/live_acceptance.py` is the bounded live check; it needs
  approved access and is not part of the unit tests.
- Keep the core install light: `huggingface_hub` and `filelock` only. Import optional packages (anndata,
  numpy, pandas, tifffile, zarr) inside the functions that use them.
- Never install unverified bytes. Every downloaded file must match its release SHA256 before it is moved into
  place. Never overwrite differing user files.
- New user-facing failures should raise an `SdaDbError` subclass with a `hint` (`src/sda_db/errors.py`). Keep
  the CLI exit codes and JSON error shape stable.
- When a release is published: add it to `src/sda_db/releases.json` with its commit, sample count and catalog
  file SHA256s, and add the public mirror only if its bytes match.
- Scientific definitions come from the published `sda-release-sample-1` contract. Do not reinterpret labels,
  infer units or add filters that the release does not record.
- Update `CHANGELOG.md`, the docs and `docs/validation.md` (recording only checks that were actually run) with
  any behaviour change.
