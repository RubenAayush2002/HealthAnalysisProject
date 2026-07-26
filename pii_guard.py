"""
pii_guard.py

Redacts personally identifiable information (PII) from health report text
BEFORE that text is sent to any third-party LLM (Gemini) or search API
(Tavily), and before it is embedded into the vector store.

Design notes
------------
- Two detection layers, combined:
    1. Deterministic regex recognizers for structured identifiers that NER
       models are unreliable on: emails, phone numbers, SSNs, MRNs/patient
       IDs, insurance/policy numbers.
    2. A local NER model (spaCy, via Presidio) for free-text PII: person
       names, addresses/locations.
  Layer 1 is exact. Layer 2 is probabilistic (like any NER model, it can
  occasionally miss an unusual name) — this is a strong mitigation, not an
  absolute guarantee. Say so to users; don't oversell it.

- Dates are intentionally NOT redacted by default. A lab report's test date
  is clinically meaningful (needed for trend/comparison features), and blanket
  date redaction would break that. If you want DOB specifically redacted,
  see `redact_dob_near_label()` below — it only redacts a date when it's
  physically near a "DOB" / "Date of Birth" label, not every date in the doc.

- Everything here runs locally (spaCy model + regex). No text is sent
  anywhere during detection. This matters for the "data minimization" story:
  even the detection step doesn't leak data, only the (redacted) output does.

Usage
-----
    from pii_guard import PIIGuard

    guard = PIIGuard()
    result = guard.redact(raw_pdf_text)

    result.redacted_text   -> safe to send to Gemini / Tavily / embeddings
    result.entities_found  -> list of (entity_type, count) for an audit log
    result.had_pii         -> bool, useful for a "we removed personal info
                               before processing" UI notice
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import phonenumbers
from presidio_analyzer import AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import SpacyRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Presidio defaults to en_core_web_lg (~800MB). We pin the lightweight
# en_core_web_sm model instead — smaller, faster to load, adequate accuracy
# for this use case. Swap to en_core_web_lg here if you need higher name/
# location recall and don't mind the extra size/latency.
_NLP_ENGINE_CONFIG = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
}


# ---------------------------------------------------------------------------
# Custom recognizers for identifiers that show up in lab reports specifically,
# which Presidio's built-ins don't know about out of the box.
# ---------------------------------------------------------------------------

_MRN_PATTERNS = [
    Pattern(name="mrn_labeled", regex=r"\b(?:MRN|Medical Record(?: No\.?| Number)?)\s*[:#]?\s*[A-Z0-9\-]{4,15}\b", score=0.9),
    Pattern(name="patient_id_labeled", regex=r"\b(?:Patient\s*ID|Patient\s*No\.?)\s*[:#]?\s*[A-Z0-9\-]{4,15}\b", score=0.9),
]

_INSURANCE_PATTERNS = [
    Pattern(name="policy_number", regex=r"\b(?:Policy|Insurance)\s*(?:No\.?|Number)?\s*[:#]?\s*[A-Z0-9\-]{5,20}\b", score=0.85),
]

_MRN_RECOGNIZER = PatternRecognizer(
    supported_entity="MEDICAL_RECORD_NUMBER",
    patterns=_MRN_PATTERNS,
    context=["mrn", "medical record", "patient id"],
)

_INSURANCE_RECOGNIZER = PatternRecognizer(
    supported_entity="INSURANCE_ID",
    patterns=_INSURANCE_PATTERNS,
    context=["policy", "insurance"],
)

# PHONE_NUMBER detection: a loose digit-grouping regex finds candidates
# (including international formats without a country code, which Presidio's
# built-in phonenumbers-based recognizer sometimes misses), but a loose
# regex alone also matches all sorts of non-phone digit runs on a lab
# report (timestamps like "12-06-2026 04:59", internal lab reference IDs,
# numeric reference ranges like "35.0-65.0"). Real-world testing against an
# actual lab report showed 100% of a naive loose-regex recognizer's matches
# on that document were false positives of exactly this kind.
#
# _GenericPhoneRecognizer below keeps the loose regex as a candidate finder,
# but validates every candidate against the `phonenumbers` library
# (Google's libphonenumber) before accepting it, and explicitly rejects
# date/timestamp-shaped strings first since those can coincidentally parse
# as "valid" numbers under some regions. It replaces Presidio's built-in
# PhoneRecognizer entirely (see _build_analyzer) rather than supplementing
# it, since running both produced duplicate, inconsistent false positives.
_PHONE_CANDIDATE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b"
)

_DATE_LIKE_RE = re.compile(
    r"^\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}(\s+\d{1,2}(:\d{2})?\s*(AM|PM)?)?\s*$",
    re.IGNORECASE,
)

# Tried in order; a candidate is accepted if ANY of these regions parses it
# as a genuinely valid (not just "possible") number. We don't know a
# report's country of origin ahead of time, so this covers the common cases
# rather than assuming one locale.
_PHONE_CANDIDATE_REGIONS = ["US", "IN", "GB", None]


def _is_plausible_phone_number(candidate: str) -> bool:
    stripped = candidate.strip()
    if _DATE_LIKE_RE.match(stripped):
        return False
    for region in _PHONE_CANDIDATE_REGIONS:
        try:
            matches = list(phonenumbers.PhoneNumberMatcher(candidate, region))
        except Exception:
            continue
        if any(
            m.raw_string.strip() == stripped and phonenumbers.is_valid_number(m.number)
            for m in matches
        ):
            return True
    return False


class _GenericPhoneRecognizer(EntityRecognizer):
    """
    Regex-based phone candidate finder, validated through `phonenumbers`
    before any match is emitted -- see module notes above. Not a
    PatternRecognizer because we need a validation step between "regex
    matched" and "emit a result", which PatternRecognizer doesn't expose.
    """

    def __init__(self) -> None:
        super().__init__(supported_entities=["PHONE_NUMBER"], name="GenericPhoneRecognizer")

    def load(self) -> None:  # pragma: no cover -- no model to load
        pass

    def analyze(self, text, entities, nlp_artifacts=None) -> list[RecognizerResult]:
        if "PHONE_NUMBER" not in entities:
            return []
        results = []
        for match in _PHONE_CANDIDATE_RE.finditer(text):
            candidate = match.group(0)
            if _is_plausible_phone_number(candidate):
                results.append(
                    RecognizerResult(
                        entity_type="PHONE_NUMBER",
                        start=match.start(),
                        end=match.end(),
                        score=0.7,
                    )
                )
        return results


_GENERIC_PHONE_RECOGNIZER = _GenericPhoneRecognizer()

# spaCy's LOCATION model reliably tags city/area names (GPE) but not full
# street addresses ("42 Elm Grove"). This regex catches the street-number +
# street-name pattern as a supplement.
_STREET_ADDRESS_PATTERNS = [
    Pattern(
        name="street_address",
        regex=r"\b\d{1,5}\s+[A-Z][a-zA-Z'.]*(?:\s+[A-Z][a-zA-Z'.]*){0,3}\s+"
        r"(?:Street|St|Road|Rd|Grove|Avenue|Ave|Lane|Ln|Drive|Dr|Court|Ct|"
        r"Way|Close|Park|Place|Pl|Terrace|Crescent|Square|Sq)\b\.?",
        score=0.75,
    ),
]
_STREET_ADDRESS_RECOGNIZER = PatternRecognizer(
    supported_entity="LOCATION",
    patterns=_STREET_ADDRESS_PATTERNS,
    context=["address", "street", "live", "residence"],
)

# ---------------------------------------------------------------------------
# Context anchoring for PERSON/LOCATION (boost-only, NOT a gate)
# ---------------------------------------------------------------------------
#
# Presidio can raise a match's confidence score when a context word (e.g.
# "patient", "referring") appears near it. It was evaluated as a way to
# GATE PERSON/LOCATION matches -- i.e. only redact names near a label,
# mirroring the DOB-near-label pattern below -- but real-world testing
# against an actual lab report showed this is unsafe: PDF-to-text
# extraction linearizes the page, and a patient's name commonly ends up
# several unrelated tokens away from its "Patient Name" label (other
# field labels and colons sit between them), while unrelated table
# content can end up textually closer. A tight window misses the real
# name; a wide window re-catches table labels.
#
# So this is wired as an ADDITIVE signal only: context words can raise an
# already-real match's score (useful when label and name genuinely are
# adjacent, e.g. "Referred By Dr: Sunie Laishram"), but the score
# threshold is NOT raised to require it. Unanchored PERSON/LOCATION
# matches still get redacted by default -- the denylist below is the
# primary defense against table/label false positives, not this.
_PERSON_LOCATION_CONTEXT_WORDS = ["patient", "referring", "referred", "physician"]

_CONTEXT_ENHANCER = LemmaContextAwareEnhancer(
    context_similarity_factor=0.35,
    min_score_with_context_similarity=0.4,
    context_prefix_count=6,
    context_suffix_count=2,
)

# ---------------------------------------------------------------------------
# Denylist: common lab-report vocabulary/labels that spaCy's PERSON/LOCATION
# NER reliably mis-tags on this kind of document (test names, method lines,
# structural field labels). Matched as a substring search against the full
# matched span (not exact-match), since spaCy sometimes grabs a test name
# plus trailing text as one span (e.g. "Alkaline Phosphatase\nMethod").
# Word-boundary anchored so short fragments don't over-match unrelated text.
# ---------------------------------------------------------------------------
_PII_DENYLIST_PATTERNS = [
    r"\bAlkaline Phosphatase\b",
    r"\bVitamin [A-Z]\d*\b",
    r"\bLDL Cholesterol\b",
    r"\bHDL Cholesterol\b",
    r"\bTotal Cholesterol\b",
    r"\bTotal Bilirubin\b",
    r"\bDirect Bilirubin\b",
    r"\bIndirect Bilirubin\b",
    r"\bTotal Protein\b",
    r"\bPlatelet\b",
    r"\bMethod\b",
    r"\bBill Date\b",
    r"\bReport Date\b",
    r"\bCollected Date\b",
    r"\bRelease Date\b",
    r"\bTest Date\b",
    r"\bRegistration Loc\b",
    r"\bProcessing Loc\b",
    r"\bClient Name\b",
    r"\bLab Ref\b",
    r"\bLab No\b",
    r"\bResult No\b",
    r"\bUHID\b",
    r"\bAge\s*/\s*Gender\b",
    r"\bMD\b",
]
_PII_DENYLIST_REGEX = "|".join(_PII_DENYLIST_PATTERNS)

# Entities we actively redact. Deliberately excludes DATE_TIME (see module
# docstring) and NRP/URL which are noisy on medical documents.
DEFAULT_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    "US_SSN",
    "CREDIT_CARD",
    "MEDICAL_RECORD_NUMBER",
    "INSURANCE_ID",
]

# Human-readable replacement tokens instead of Presidio's default <ENTITY_TYPE>
# so the redacted text still reads naturally to the downstream LLM.
_REPLACEMENT_LABELS = {
    "PERSON": "[PATIENT NAME REDACTED]",
    "EMAIL_ADDRESS": "[EMAIL REDACTED]",
    "PHONE_NUMBER": "[PHONE REDACTED]",
    "LOCATION": "[ADDRESS REDACTED]",
    "US_SSN": "[SSN REDACTED]",
    "CREDIT_CARD": "[CARD NUMBER REDACTED]",
    "MEDICAL_RECORD_NUMBER": "[MRN REDACTED]",
    "INSURANCE_ID": "[INSURANCE ID REDACTED]",
}

_DOB_NEAR_LABEL_RE = re.compile(
    r"(?:date of birth|dob)\s*[:#]?\s*"
    r"(\d{1,4}[/\-. ]\d{1,2}[/\-. ]\d{1,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


@dataclass
class PIIRedactionResult:
    redacted_text: str
    entities_found: dict[str, int] = field(default_factory=dict)

    @property
    def had_pii(self) -> bool:
        return sum(self.entities_found.values()) > 0

    def summary(self) -> str:
        """Short line suitable for showing the user, e.g. in a status block."""
        if not self.had_pii:
            return "No personal identifiers detected."
        parts = [f"{count} {etype.replace('_', ' ').lower()}" for etype, count in self.entities_found.items()]
        return "Removed before processing: " + ", ".join(parts) + "."


class PIIGuard:
    """
    Loads the NER model once (expensive, ~50-200ms+ per call otherwise) and
    reuses it. Wrap the instance in @st.cache_resource in app.py, same
    pattern as get_llm()/get_embeddings().
    """

    def __init__(self, entities: list[str] | None = None):
        self.entities = entities or DEFAULT_ENTITIES
        self._analyzer = _build_analyzer()
        self._anonymizer = AnonymizerEngine()

    def redact(self, text: str, redact_dob: bool = True) -> PIIRedactionResult:
        if not text or not text.strip():
            return PIIRedactionResult(redacted_text=text, entities_found={})

        working_text = text
        if redact_dob:
            working_text = _DOB_NEAR_LABEL_RE.sub(
                lambda m: m.group(0).replace(m.group(1), "[DOB REDACTED]"), working_text
            )

        results = self._analyzer.analyze(
            text=working_text,
            entities=self.entities,
            language="en",
            context=_PERSON_LOCATION_CONTEXT_WORDS,
            allow_list=[_PII_DENYLIST_REGEX],
            allow_list_match="regex",
        )

        operators = {
            etype: OperatorConfig("replace", {"new_value": label})
            for etype, label in _REPLACEMENT_LABELS.items()
        }

        anonymized = self._anonymizer.anonymize(
            text=working_text,
            analyzer_results=results,
            operators=operators,
        )

        counts: dict[str, int] = {}
        for r in results:
            counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
        if "[DOB REDACTED]" in anonymized.text:
            counts["DOB"] = anonymized.text.count("[DOB REDACTED]")

        return PIIRedactionResult(redacted_text=anonymized.text, entities_found=counts)


@lru_cache(maxsize=1)
def _build_analyzer() -> AnalyzerEngine:
    nlp_engine = NlpEngineProvider(nlp_configuration=_NLP_ENGINE_CONFIG).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, context_aware_enhancer=_CONTEXT_ENHANCER)

    # Swap the default SpacyRecognizer (all entity types, no context words,
    # registered automatically by AnalyzerEngine) for one scoped to
    # PERSON/LOCATION with context words attached -- the context-aware
    # enhancer only boosts a match if its recognizer has a non-empty
    # `context` list to check against.
    analyzer.registry.remove_recognizer("SpacyRecognizer")
    analyzer.registry.add_recognizer(
        SpacyRecognizer(
            supported_entities=["PERSON", "LOCATION"],
            context=_PERSON_LOCATION_CONTEXT_WORDS,
        )
    )

    # Presidio's built-in PhoneRecognizer (also phonenumbers-backed) is
    # registered automatically, but its date-vs-phone-number disambiguation
    # is weaker than _GenericPhoneRecognizer's (see notes above the latter)
    # -- running both produced duplicate, inconsistent false positives on
    # timestamps. _GenericPhoneRecognizer supersedes it as the single
    # PHONE_NUMBER source.
    analyzer.registry.remove_recognizer("PhoneRecognizer")

    analyzer.registry.add_recognizer(_MRN_RECOGNIZER)
    analyzer.registry.add_recognizer(_INSURANCE_RECOGNIZER)
    analyzer.registry.add_recognizer(_GENERIC_PHONE_RECOGNIZER)
    analyzer.registry.add_recognizer(_STREET_ADDRESS_RECOGNIZER)
    return analyzer
