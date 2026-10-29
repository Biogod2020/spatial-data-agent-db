# Contributing

This repository owns user documentation, release access, downloads and reading
examples. Discovery, classification, standardization, scoring and publication
remain in the main SpatialDataAgent repository.

Keep the client install independent of cluster paths, production databases,
model credentials and internal management tools. Scientific output definitions
come from the published `sda-release-sample-1` contract. Schema changes require
checking their published implementation and updating provenance.

Run `python -m unittest discover -s tests -v` after installing the client and NumPy.
Use synthetic fixtures for download/integrity tests; tests must not fetch the
real dataset or require approval credentials. Live read checks should be explicit,
bounded, fixed to a commit and recorded separately.

Stream tests use thread events to check prefetch overlap and capacity waits;
avoid timing-only assertions. Keep active/prefetched samples pinned, reserve
payload space before downloads, and evict only entries in an exclusively owned
cache. Tests must preserve unmanaged directories and permanent downloads.

The main repository references this repository as a Git submodule at
`spatial-data-agent-db/`. Commit and push this repository first, then update its
pointer in the main repository. From a main-repository clone:

```bash
git submodule update --init spatial-data-agent-db
```

Opening this repository directly is also supported; its packaging and CI do not
depend on the parent checkout. Do not commit downloaded matrices, images,
credentials or private host paths.
