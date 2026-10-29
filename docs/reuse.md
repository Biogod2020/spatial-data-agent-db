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

- Dataset identifier `Biogod/SpatialDataAgent-DB` and full Hugging Face commit.
- Toolkit version, selected GSMs, catalog SHA256 and download receipts.
- Relevant original GEO accessions and original study/publication citations.
- All additional feature, observation, image or QC inclusion rules.

Database access and reuse follow the dataset card and original source conditions
at the commit used. This toolkit does not relicense the original studies or
grant access to data. No paper DOI or blanket data license is invented here;
use the citation information supplied by the database and the original studies.
