"""Catalog filtering and ranked keyword search; deterministic, offline and model-free."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import difflib
import math
import re
from typing import Any, Iterable, Sequence

from .errors import UnknownFieldError, UnknownValueError

# Fields whose catalog value holds several members.
LIST_SEPARATORS = {"gse": "+", "flags": ";", "qc_flags": ";", "drop_features": ";", "aliases": ";",
                   "organism": "\n;",
                   # metadata v2 list columns (joined with ';' in samples_metadata_v2.csv)
                   "organisms": ";", "organism_taxon_ids": ";", "co_detected_organisms": ";",
                   "disease_ontology_ids": ";", "disease_ontology_names": ";", "pubmed_ids": ";", "dois": ";",
                   "annotation_low_confidence": ";", "metadata_flags": ";"}
NUMERIC_FIELDS = frozenset({"n_obs", "n_vars", "um_per_unit", "image_um_per_px", "annotation_min_confidence"})
BOOLEAN_FIELDS = frozenset({"has_image", "has_spatial_um", "frozen_paper_evaluation_eligible", "xenograft",
                            "multi_species"})
# Small, release-defined vocabularies: an unknown value is an error with suggestions.
CONTROLLED_FIELDS = frozenset({
    "platform", "organism", "source_pipeline", "observation_unit", "expression_state", "verification",
    "coordinate_units", "um_basis", "image_stain", "image_stain_basis", "image_registration", "segmentation",
    "molecules", "molecules_use", "review_decision", "group_kind", "split", "counts_ready", "review_kind",
    "flags", "qc_flags"}) | BOOLEAN_FIELDS
# Controlled only when a release carries metadata v2 (in v3, 'sex' is free text as reported).
V2_CONTROLLED_FIELDS = frozenset({
    "organ", "organ_source", "sample_kind", "disease_status", "disease_status_source", "life_stage", "sex",
    "preservation", "assay_type", "assay_name", "annotation_version", "annotation_low_confidence", "metadata_flags",
    "organism_common_name", "feature_species", "platform_basis"})

# Common names map to the scientific names that GEO records. Applied only when present.
ORGANISM_NAMES = {
    "human": "Homo sapiens", "mouse": "Mus musculus", "mice": "Mus musculus", "murine": "Mus musculus",
    "rat": "Rattus norvegicus", "zebrafish": "Danio rerio", "pig": "Sus scrofa", "porcine": "Sus scrofa",
    "rhesus": "Macaca mulatta", "rhesus macaque": "Macaca mulatta", "cynomolgus": "Macaca fascicularis",
    "crab-eating macaque": "Macaca fascicularis", "chicken": "Gallus gallus", "dog": "Canis lupus familiaris",
    "axolotl": "Ambystoma mexicanum", "barley": "Hordeum vulgare", "arabidopsis": "Arabidopsis thaliana",
    "maize": "Zea mays", "corn": "Zea mays", "cow": "Bos taurus", "cattle": "Bos taurus",
    "rabbit": "Oryctolagus cuniculus", "guinea pig": "Cavia porcellus", "hamster": "Mesocricetus auratus",
    "salmon": "Salmo salar", "tomato": "Solanum lycopersicum", "wheat": "Triticum aestivum",
    "baboon": "Papio anubis", "frog": "Xenopus laevis", "xenopus": "Xenopus laevis",
}
PLATFORM_NAMES = {
    "10x visium": "Visium", "visium sd": "Visium", "visium cytassist": "Visium", "10x visium hd": "Visium HD",
    "10x xenium": "Xenium", "xenium prime": "Xenium", "nanostring cosmx": "CosMx", "cosmx smi": "CosMx",
    "smi": "CosMx", "merfish": "MERFISH/MERSCOPE", "merscope": "MERFISH/MERSCOPE",
    "vizgen merscope": "MERFISH/MERSCOPE", "st": "ST (legacy)", "legacy st": "ST (legacy)",
    "st legacy": "ST (legacy)", "spatial transcriptomics": "ST (legacy)", "slide-seqv2": "Slide-seq",
    "slideseq": "Slide-seq", "stereoseq": "Stereo-seq", "stomics": "Stereo-seq", "dbit": "DBiT-seq",
    "resolve": "Molecular Cartography", "bmk": "BMK S1000",
}
STAIN_NAMES = {"he": "H&E", "hne": "H&E", "h and e": "H&E", "hematoxylin and eosin": "H&E",
               "dapi": "fluorescence: DAPI"}
SAMPLE_KIND_NAMES = {"tumour": "tumour tissue", "tumor": "tumour tissue", "tumor tissue": "tumour tissue",
                     "cancer": "tumour tissue", "primary": "primary tissue", "pdx": "xenograft",
                     "embryo": "whole embryo/organism", "whole embryo": "whole embryo/organism",
                     "whole organism": "whole embryo/organism", "cell line": "cell culture", "culture": "cell culture",
                     "metastatic": "metastasis"}
DISEASE_STATUS_NAMES = {"healthy": "normal", "control": "normal", "diseased": "disease", "unknown": "not stated",
                        "treated": "perturbation", "intervention": "perturbation"}
LIFE_STAGE_NAMES = {"infant": "neonatal or infant", "neonatal": "neonatal or infant", "neonate": "neonatal or infant",
                    "newborn": "neonatal or infant", "elderly": "aged", "old": "aged", "embryo": "embryonic",
                    "fetus": "fetal", "foetal": "fetal", "unknown": "not stated"}
SEX_NAMES = {"m": "male", "f": "female", "men": "male", "women": "female"}
PRESERVATION_NAMES = {"frozen": "fresh frozen", "fresh-frozen": "fresh frozen", "oct": "fresh frozen",
                      "paraffin": "FFPE", "formalin-fixed paraffin-embedded": "FFPE", "fixed-frozen": "fixed frozen"}
ASSAY_TYPE_NAMES = {"imaging": "imaging-based", "sequencing": "sequencing-based", "image-based": "imaging-based"}
ORGAN_NAMES = {"mammary": "breast", "mammary gland": "breast", "muscle": "skeletal muscle", "marrow": "bone marrow",
               "nerve": "peripheral nerve", "artery": "blood vessel", "aorta": "blood vessel", "embryo": "embryo (whole)",
               "large intestine": "colon"}
VALUE_ALIASES = {"organism": ORGANISM_NAMES, "platform": PLATFORM_NAMES, "image_stain": STAIN_NAMES}
# Used only for releases with metadata v2, so paper-release filters behave exactly as before.
V2_VALUE_ALIASES = {"sample_kind": SAMPLE_KIND_NAMES, "disease_status": DISEASE_STATUS_NAMES,
                    "life_stage": LIFE_STAGE_NAMES, "sex": SEX_NAMES, "preservation": PRESERVATION_NAMES,
                    "assay_type": ASSAY_TYPE_NAMES, "organ": ORGAN_NAMES}
COMMON_WORDS = {
    "Homo sapiens": "human", "Mus musculus": "mouse mice murine", "Rattus norvegicus": "rat",
    "Macaca fascicularis": "macaque cynomolgus monkey primate", "Macaca mulatta": "macaque rhesus monkey primate",
    "Sus scrofa": "pig porcine", "Danio rerio": "zebrafish", "Gallus gallus": "chicken",
    "Canis lupus familiaris": "dog canine", "Ambystoma mexicanum": "axolotl", "Hordeum vulgare": "barley plant",
    "Arabidopsis thaliana": "arabidopsis plant", "Zea mays": "maize corn plant", "Bos taurus": "cow bovine cattle",
    "Oryctolagus cuniculus": "rabbit", "Cavia porcellus": "guinea pig", "Mesocricetus auratus": "hamster",
    "Xenopus laevis": "frog", "Salmo salar": "salmon fish", "Solanum lycopersicum": "tomato plant",
    "Triticum aestivum": "wheat plant", "Papio anubis": "baboon primate", "xenograft": "xenograft pdx",
}
PLATFORM_WORDS = {"CosMx": "smi nanostring imaging", "Stereo-seq": "stomics", "MERFISH/MERSCOPE": "vizgen imaging",
                  "Xenium": "imaging", "Molecular Cartography": "resolve imaging", "ST (legacy)": "legacy",
                  "Visium HD": "visiumhd"}
STAIN_WORDS = {"H&E": "hematoxylin eosin", "DAPI": "fluorescence nuclei", "immunofluorescence": "if"}
STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it of on or our that the their these this those to was "
    "we were which with using use used via vs versus data dataset datasets sample samples study studies "
    "spatial transcriptomics transcriptomic transcriptome omics profiling profile profiles".split())
QUERY_SYNONYMS = {"he": "h&e", "hne": "h&e"}

_TOKEN = re.compile(r"[a-z0-9]+(?:&[a-z0-9]+)?")
_HYPHENATED = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
_NORMALIZE = re.compile(r"[\s\-_()/.,;:+'\"]+")


def normalize_value(value: Any) -> str:
    """Comparison key: case, spaces, hyphens, brackets and slashes are ignored."""
    return _NORMALIZE.sub("", str(value).casefold())


def stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, *, compounds: bool = False) -> list[str]:
    """Lower-case word tokens with light plural stemming; keeps 'h&e' as one token."""
    lowered = str(text).casefold()
    tokens = [stem(t) for t in _TOKEN.findall(lowered) if (len(t) > 1 or t.isdigit()) and t not in STOPWORDS]
    if compounds:
        tokens += [stem(word.replace("-", "")) for word in _HYPHENATED.findall(lowered)]
    return tokens


def members(field: str, value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value)
    if field in LIST_SEPARATORS:
        parts = re.split("[" + re.escape(LIST_SEPARATORS[field]) + "]", text)
        return [p.strip() for p in parts if p.strip()]
    return [text] if text != "" else []


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).casefold()
    if text in {"true", "yes", "1", "y", "t"}:
        return True
    if text in {"false", "no", "0", "n", "f"}:
        return False
    return None


OPERATORS = ("!~", "!=", ">=", "<=", "=", "~", ">", "<")


@dataclass(frozen=True)
class Condition:
    """One filter: field, operator and the accepted values (OR within one condition)."""

    field: str
    op: str
    values: tuple[Any, ...]

    def describe(self) -> str:
        joined = "|".join("" if v is None else str(v) for v in self.values)
        return f"{self.field}{self.op}{joined}"


def parse_where(expression: str) -> Condition:
    """Parse FIELD OP VALUE where OP is =, !=, ~ (contains), !~, >, >=, < or <=.

    '|' separates alternatives (OR). An empty value with '=' selects missing values,
    and with '!=' selects present values.
    """
    text = expression.strip()
    best: tuple[int, str] | None = None
    for op in OPERATORS:
        index = text.find(op)
        if index > 0 and (best is None or index < best[0] or (index == best[0] and len(op) > len(best[1]))):
            best = (index, op)
    if best is None:
        raise UnknownFieldError(f"Cannot parse filter {expression!r}",
                                hint="Use FIELD=VALUE, FIELD!=VALUE, FIELD~TEXT, FIELD>=NUMBER; '|' means OR.")
    index, op = best
    field = text[:index].strip()
    raw = text[index + len(op):].strip()
    values: tuple[Any, ...] = tuple(v.strip() for v in raw.split("|")) if raw else ("",)
    return Condition(field, op, values)


def condition_from_kwarg(field: str, value: Any) -> Condition:
    if isinstance(value, (list, tuple, set, frozenset)):
        return Condition(field, "=", tuple(value))
    return Condition(field, "=", (value,))


class Vocabulary:
    """Distinct values per field, for validation, aliases and suggestions."""

    def __init__(self, rows: Sequence[dict[str, Any]], fields: Iterable[str], *,
                 controlled: frozenset[str] = CONTROLLED_FIELDS,
                 aliases: dict[str, dict[str, str]] | None = None) -> None:
        self.fields = list(fields)
        self._values: dict[str, Counter[str]] = {}
        self._rows = rows
        self.controlled = controlled
        self.aliases = VALUE_ALIASES if aliases is None else aliases
        self.cache: dict[str, Any] = {}

    @property
    def rows(self) -> Sequence[dict[str, Any]]:
        return self._rows

    def values(self, field: str) -> Counter[str]:
        if field not in self._values:
            counter: Counter[str] = Counter()
            for row in self._rows:
                for member in members(field, row.get(field)):
                    counter[member] += 1
            self._values[field] = counter
        return self._values[field]

    def check_field(self, field: str) -> None:
        if field not in self.fields:
            close = difflib.get_close_matches(field, self.fields, n=3, cutoff=0.5)
            hint = (f"Did you mean {', '.join(close)}?" if close else "") + " List fields with 'sda-db fields'."
            raise UnknownFieldError(f"Unknown field {field!r}", hint=hint.strip())

    def canonical(self, field: str, value: Any) -> list[str]:
        """Catalog values equal to `value` after normalization and aliasing."""
        known = self.values(field)
        key = normalize_value(value)
        aliases = self.aliases.get(field, {})
        table = {normalize_value(k): v for k, v in aliases.items()}
        alias = table.get(key) or table.get(stem(key))
        targets = {key, stem(key)} | ({normalize_value(alias)} if alias else set())
        return [v for v in known if normalize_value(v) in targets]

    def suggest(self, field: str, value: Any, n: int = 5) -> list[str]:
        known = list(self.values(field))
        key = normalize_value(value)
        by_key = {normalize_value(v): v for v in known}
        close = [by_key[k] for k in difflib.get_close_matches(key, list(by_key), n=n, cutoff=0.6)]
        table = {normalize_value(k): v for k, v in self.aliases.get(field, {}).items()}
        for alias_key, target in table.items():
            if target in known and target not in close and (difflib.SequenceMatcher(None, key, alias_key).ratio() > 0.7):
                close.insert(0, target)
        contains = [v for v in known if key and key in normalize_value(v) and v not in close]
        ranked = close + sorted(contains, key=lambda v: -self.values(field)[v])
        return ranked[:n]


def compile_condition(condition: Condition, vocabulary: Vocabulary, warnings: list[str]):
    """Return a row predicate; validate fields and controlled values first."""
    field, op = condition.field, condition.op
    vocabulary.check_field(field)
    if op in {">", ">=", "<", "<="}:
        if len(condition.values) != 1 or _number(condition.values[0]) is None:
            raise UnknownValueError(f"{field}{op} needs one number", hint=f"Example: {field}{op}1000")
        limit = float(condition.values[0])
        compare = {">": float.__gt__, ">=": float.__ge__, "<": float.__lt__, "<=": float.__le__}[op]

        def numeric(row: dict[str, Any]) -> bool:
            number = _number(row.get(field))
            return number is not None and compare(number, limit)
        return numeric
    if op in {"~", "!~"}:
        needles = [str(v).casefold() for v in condition.values if str(v) != ""]
        if not needles:
            raise UnknownValueError(f"{field}{op} needs text to look for")

        def contains(row: dict[str, Any]) -> bool:
            text = "" if row.get(field) is None else str(row.get(field)).casefold()
            hit = any(n in text for n in needles)
            return hit if op == "~" else not hit
        return contains
    # Equality on (possibly multi-valued) fields.
    wanted_missing = any(v is None or v == "" for v in condition.values)
    present_values = [v for v in condition.values if not (v is None or v == "")]
    if field in BOOLEAN_FIELDS:
        targets_bool = set()
        for value in present_values:
            decoded = _boolean(value)
            if decoded is None:
                raise UnknownValueError(f"{field} takes true or false, not {value!r}")
            targets_bool.add(decoded)

        def boolean(row: dict[str, Any]) -> bool:
            actual = _boolean(row.get(field)) if row.get(field) not in (None, "") else None
            hit = (actual is None and wanted_missing) or (actual is not None and actual in targets_bool)
            return hit if op == "=" else not hit
        return boolean
    if field in NUMERIC_FIELDS:
        numbers = set()
        for value in present_values:
            if _number(value) is None:
                raise UnknownValueError(f"{field} is numeric; {value!r} is not a number")
            numbers.add(float(value))

        def number_equal(row: dict[str, Any]) -> bool:
            actual = _number(row.get(field))
            hit = (actual is None and wanted_missing) or (actual is not None and actual in numbers)
            return hit if op == "=" else not hit
        return number_equal
    accepted: set[str] = set()
    for value in present_values:
        matches = vocabulary.canonical(field, value)
        if not matches:
            suggestions = vocabulary.suggest(field, value)
            if field in vocabulary.controlled:
                hint = (f"Closest values: {', '.join(repr(s) for s in suggestions)}. " if suggestions else "")
                raise UnknownValueError(f"No sample has {field}={value!r} in this release",
                                        hint=hint + f"List values with 'sda-db values {field}' (MCP: field_values).")
            note = f"No sample has {field}={value!r} exactly"
            if suggestions:
                note += f"; similar values: {', '.join(repr(s) for s in suggestions[:3])}"
            warnings.append(note + f". Use '{field}~{value}' for a substring match.")
        accepted.update(normalize_value(m) for m in matches)

    def equal(row: dict[str, Any]) -> bool:
        items = members(field, row.get(field))
        hit = (not items and wanted_missing) or any(normalize_value(item) in accepted for item in items)
        return hit if op == "=" else not hit
    return equal


MONDO_ID = re.compile(r"mondo[:_ ]?([0-9]{7})\Z", re.IGNORECASE)


def disease_term_predicate(values: Sequence[Any], vocabulary: Vocabulary, warnings: list[str]):
    """Metadata v2 disease filter: a MONDO ID exactly, or a MONDO name exactly or as a case-insensitive
    substring (e.g. 'fibrosis' matches 'pulmonary fibrosis'). Ancestor terms are not expanded."""
    ids: set[str] = set()
    needles: list[str] = []
    names = list(vocabulary.values("disease_ontology_names"))
    known_ids = vocabulary.values("disease_ontology_ids")
    for value in values:
        text = str(value).strip()
        if not text:
            raise UnknownValueError("disease needs a MONDO ID or a disease name")
        found = MONDO_ID.fullmatch(text)
        if found:
            ids.add(f"MONDO:{found.group(1)}")
            if f"MONDO:{found.group(1)}" not in known_ids:
                warnings.append(f"No sample has the disease term MONDO:{found.group(1)} (ancestors are not expanded; "
                                "list terms with 'sda-db values disease_ontology_ids')")
            continue
        needle = text.casefold()
        needles.append(needle)
        if not any(needle in name.casefold() for name in names):
            close = difflib.get_close_matches(needle, [n.casefold() for n in names], n=3, cutoff=0.6)
            warnings.append(f"No sample has a disease term containing {text!r}"
                            + (f"; similar terms: {', '.join(repr(c) for c in close)}" if close else "")
                            + ". Use where='disease~TEXT' to search the disease text as reported in GEO.")

    def disease(row: dict[str, Any]) -> bool:
        if ids and ids & set(members("disease_ontology_ids", row.get("disease_ontology_ids"))):
            return True
        row_names = [n.casefold() for n in members("disease_ontology_names", row.get("disease_ontology_names"))]
        return any(needle in name for needle in needles for name in row_names)
    return disease


def organism_predicate(values: Sequence[Any], vocabulary: Vocabulary, warnings: list[str]):
    """Metadata v2 organism filter: scientific name, NCBI common name, taxon ID or a common alias ('human');
    any listed host species of a sample counts (xenografts and mixed entries list several)."""
    if "species_synonyms" not in vocabulary.cache:
        synonyms: dict[str, set[str]] = defaultdict(set)
        labels: dict[str, str] = {}

        def add(key: Any, name: str) -> None:
            if key:
                synonyms[normalize_value(key)].add(name)
                labels.setdefault(normalize_value(key), str(key))
        for row in vocabulary.rows:
            first = row.get("organism")
            if first:
                for key in (first, row.get("organism_common_name"), row.get("organism_taxon_id")):
                    add(key, first)
            listed = members("organisms", row.get("organisms"))
            taxa = members("organism_taxon_ids", row.get("organism_taxon_ids"))
            for name, taxon in zip(listed, taxa if len(taxa) == len(listed) else [None] * len(listed)):
                add(name, name)
                add(taxon, name)
        vocabulary.cache["species_synonyms"] = (dict(synonyms), labels)
    synonyms, labels = vocabulary.cache["species_synonyms"]
    aliases = {normalize_value(k): v for k, v in ORGANISM_NAMES.items()}
    accepted: set[str] = set()
    for value in values:
        key = normalize_value(value)
        names = set().union(*(synonyms.get(k, set()) for k in {key, stem(key)}))
        if not names and key in aliases:
            names = synonyms.get(normalize_value(aliases[key]), set())
        if not names:
            close = difflib.get_close_matches(key, list(labels), n=5, cutoff=0.6)
            hint = (f"Closest values: {', '.join(repr(labels[c]) for c in close)}. " if close else "")
            raise UnknownValueError(f"No sample has organism={value!r} in this release",
                                    hint=hint + "List values with 'sda-db values organism' or organism_common_name.")
        accepted |= {normalize_value(n) for n in names}

    def organism(row: dict[str, Any]) -> bool:
        return any(normalize_value(m) in accepted
                   for m in members("organisms", row.get("organisms")) or members("organism", row.get("organism")))
    return organism


# ---------------------------------------------------------------------------
# Ranked keyword search (BM25 over sample fields plus the sample's study text)

SAMPLE_FIELD_WEIGHTS = {"gsm": 3.0, "gse": 3.0, "tissue": 2.5, "disease": 2.5, "condition": 1.5,
                        "organism": 2.0, "platform": 2.0, "technology": 1.0, "image_stain": 1.5,
                        "observation_unit": 1.0,
                        # metadata v2 (absent in releases without it, whose index is unchanged)
                        "organ": 2.0, "tissue_ontology_name": 2.0, "disease_ontology_names": 2.5,
                        "sample_kind": 1.0, "life_stage": 1.0, "assay_name": 1.5, "assay_type": 0.5,
                        "organism_common_name": 1.0, "preservation": 1.0}
V2_INDEXED = frozenset({"organ", "tissue_ontology_name", "disease_ontology_names", "sample_kind", "life_stage",
                        "assay_name", "assay_type", "organism_common_name", "preservation"})
UNINFORMATIVE = frozenset({"not stated", "unknown", "other", "not named in GEO",
                           "other (named, not in list)"})  # v2 placeholders, not indexed
STUDY_FIELD_WEIGHTS = {"title": 2.0, "design": 1.5, "summary": 1.0}
K1, B = 1.2, 0.75


@dataclass
class TextQuery:
    terms: list[str]
    phrases: list[str]
    excluded: list[str]
    prefixes: list[str]
    excluded_prefixes: list[str]
    excluded_phrases: list[str]

    @property
    def negative(self) -> bool:
        return bool(self.excluded or self.excluded_prefixes or self.excluded_phrases)


MAX_PREFIX_EXPANSION = 500


def parse_text_query(text: str) -> TextQuery:
    """Split a query into terms, "phrases", prefix* terms and their -negations."""
    phrases: list[str] = []
    excluded_phrases: list[str] = []
    for sign, phrase in re.findall(r'(-?)"([^"]+)"', text):
        if phrase.strip():
            (excluded_phrases if sign else phrases).append(" ".join(phrase.casefold().split()))
    rest = re.sub(r'-?"[^"]*"', " ", text)
    terms: list[str] = []
    excluded: list[str] = []
    prefixes: list[str] = []
    excluded_prefixes: list[str] = []
    for word in rest.split():
        negative = word.startswith("-") and len(word) > 1
        word = word[1:] if negative else word
        if word.endswith("*") and len(word) > 2:
            (excluded_prefixes if negative else prefixes).extend(tokenize(word[:-1]))
            continue
        tokens = [QUERY_SYNONYMS.get(t, t) for t in tokenize(word)]
        (excluded if negative else terms).extend(tokens)
    return TextQuery(list(dict.fromkeys(terms)), phrases, excluded, prefixes, excluded_prefixes, excluded_phrases)


class TextIndex:
    """Inverted index over sample fields and study title/design/summary text."""

    def __init__(self, rows: Sequence[dict[str, Any]], studies: dict[str, dict[str, Any]]) -> None:
        self.study_of: dict[str, str] = {}
        self.samples_of: dict[str, list[str]] = defaultdict(list)
        self.sample_tf: dict[str, Counter[str]] = {}
        self.study_tf: dict[str, Counter[str]] = {}
        self.sample_text: dict[str, str] = {}
        self.study_text: dict[str, str] = {}
        self.sample_postings: dict[str, set[str]] = defaultdict(set)
        self.study_postings: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            gsm, gse = row["gsm"], row["published_gse"]
            self.study_of[gsm] = gse
            self.samples_of[gse].append(gsm)
            counter: Counter[str] = Counter()
            parts = []
            for field, weight in SAMPLE_FIELD_WEIGHTS.items():
                value = row.get(field)
                if value in (None, "") or (field in V2_INDEXED and value in UNINFORMATIVE):
                    continue
                text = str(value)
                if field == "organism":
                    text += " " + " ".join(w for name, w in COMMON_WORDS.items() if name in text)
                elif field == "platform":
                    text += " " + PLATFORM_WORDS.get(text, "")
                elif field == "image_stain":
                    text += " " + " ".join(w for name, w in STAIN_WORDS.items() if name in text)
                parts.append(text)
                for token in tokenize(text, compounds=True):
                    counter[token] += weight
            self.sample_tf[gsm] = counter
            self.sample_text[gsm] = " ".join(parts).casefold()
            for token in counter:
                self.sample_postings[token].add(gsm)
        for gse in self.samples_of:
            study = studies.get(gse) or {}
            counter = Counter()
            parts = []
            for field, weight in STUDY_FIELD_WEIGHTS.items():
                value = study.get(field)
                if not value:
                    continue
                parts.append(str(value))
                for token in tokenize(str(value), compounds=True):
                    counter[token] += weight
            self.study_tf[gse] = counter
            self.study_text[gse] = " ".join(parts).casefold()
            for token in counter:
                self.study_postings[token].add(gse)
        self.vocabulary = set(self.sample_postings) | set(self.study_postings)
        lengths = [sum(self.sample_tf[g].values()) + sum(self.study_tf[self.study_of[g]].values())
                   for g in self.study_of]
        self.average_length = (sum(lengths) / len(lengths)) if lengths else 1.0
        self.size = len(lengths)

    def matching(self, term: str) -> set[str]:
        found = set(self.sample_postings.get(term, ()))
        for gse in self.study_postings.get(term, ()):
            found.update(self.samples_of[gse])
        return found

    def phrase_matching(self, phrase: str) -> set[str]:
        found = {g for g, text in self.sample_text.items() if phrase in text}
        for gse, text in self.study_text.items():
            if phrase in text:
                found.update(self.samples_of[gse])
        return found

    def expand(self, prefix: str, report: dict[str, Any]) -> list[str]:
        expanded = sorted(t for t in self.vocabulary if t.startswith(prefix))
        if len(expanded) > MAX_PREFIX_EXPANSION:
            report.setdefault("notes", []).append(
                f"{prefix}* expands to {len(expanded)} words; only the first {MAX_PREFIX_EXPANSION} were used")
        return expanded[:MAX_PREFIX_EXPANSION]

    def search(self, query: str, *, within: Iterable[str], match: str = "all"
               ) -> tuple[dict[str, float], dict[str, Any]]:
        """Return BM25 scores for matching samples and a report of how each term matched.

        report['operative'] is False when the query had nothing to apply (only generic or
        unmatched words and no exclusions); callers should then leave the selection unfiltered.
        """
        parsed = parse_text_query(query)
        report: dict[str, Any] = {"terms": {}, "ignored_terms": [], "phrases": {}}
        sets: list[set[str]] = []
        scoring_terms: list[str] = []
        for term in parsed.terms:
            hits = self.matching(term)
            report["terms"][term] = len(hits)
            if hits:
                sets.append(hits)
                scoring_terms.append(term)
            else:
                report["ignored_terms"].append(term)
        for prefix in parsed.prefixes:
            expanded = self.expand(prefix, report)
            hits: set[str] = set()
            for token in expanded:
                hits |= self.matching(token)
            report["terms"][prefix + "*"] = len(hits)
            if hits:
                sets.append(hits)
                scoring_terms.extend(expanded)
            else:
                report["ignored_terms"].append(prefix + "*")
        for phrase in parsed.phrases:
            hits = self.phrase_matching(phrase)
            report["phrases"][phrase] = len(hits)
            if hits:
                sets.append(hits)
                scoring_terms.extend(tokenize(phrase))
            else:
                report["ignored_terms"].append(f'"{phrase}"')
        pool = set(within)
        positive = bool(parsed.terms or parsed.phrases or parsed.prefixes)
        if not sets:
            candidates: set[str] = set() if positive else set(pool)
        elif match == "any":
            candidates = set().union(*sets) & pool
        else:
            candidates = set.intersection(*sets) & pool
        for term in parsed.excluded:
            candidates -= self.matching(term)
        for prefix in parsed.excluded_prefixes:
            for token in self.expand(prefix, report):
                candidates -= self.matching(token)
        for phrase in parsed.excluded_phrases:
            candidates -= self.phrase_matching(phrase)
        report["excluded"] = parsed.excluded + [p + "*" for p in parsed.excluded_prefixes] + [
            f'"{p}"' for p in parsed.excluded_phrases]
        report["operative"] = bool(sets) or parsed.negative
        report["all_ignored"] = positive and not sets
        scores: dict[str, float] = {}
        frequencies = {t: len(self.matching(t)) for t in set(scoring_terms)}
        for gsm in candidates:
            study_counter = self.study_tf[self.study_of[gsm]]
            sample_counter = self.sample_tf[gsm]
            length = sum(sample_counter.values()) + sum(study_counter.values())
            score = 0.0
            for term in set(scoring_terms):
                tf = sample_counter.get(term, 0.0) + study_counter.get(term, 0.0)
                if not tf:
                    continue
                df = frequencies[term]
                idf = math.log(1 + (self.size - df + 0.5) / (df + 0.5))
                # Sample-level evidence (tissue, disease, IDs) outranks shared study text.
                boost = 1.0 + (0.5 if sample_counter.get(term) else 0.0)
                score += boost * idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.average_length))
            scores[gsm] = round(score, 4)
        return scores, report
