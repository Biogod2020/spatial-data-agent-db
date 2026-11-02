# Reuse, cohorts and citation

Use the version and per-sample annotations that match the files you downloaded.
The database combines platforms and source pipelines; an observation can be a
cell, spot, bin or bead. Preserve `observation_unit` rather than treating all
observations as cells.

For predictive evaluation, start with the released `split`. Keep each GSE's
membership together, including multi-series membership, and account for
`content_group` and `aliases`. Donor identity and redeposition may require further
study-specific checks. Randomly splitting observations from the same section
can leak study, tissue and image information across evaluation sets.

Inspect `expression_state` and its verification before count-based methods.
Apply `quality.drop_features` explicitly to an analysis copy, and document your
handling of `flags`, `qc_flags` and review states. The client preserves source
values and does not silently turn annotations into filters. Reprocessing or
harmonizing metadata should produce a derived analysis with its own provenance.

Record these items with your analysis:

- Dataset identifier `Biogod/SpatialDataAgent-DB`, release name and full Hugging Face commit.
- Toolkit version, selected GSMs, catalog SHA256 and download receipts. `selection.save_selection()` or
  `sda-db search ... --output cohort.json` records all of these, and `Catalog.from_selection()` replays them.
- Relevant original GEO accessions and original study/publication citations.
- All additional feature, observation, image or QC inclusion rules.

`sda-db cite` (or `catalog.cite()`) writes BibTeX for three things: the database paper (bioRxiv,
doi:10.64898/2026.05.27.727615, metadata from Crossref), the release with its commit, and every GEO series of
the selection, with the PubMed IDs and DOIs that the release records.

Database access and reuse follow the dataset card and the original source conditions at the commit used.
This toolkit does not relicense the original studies or grant access to data, and it adds no blanket data
license.
