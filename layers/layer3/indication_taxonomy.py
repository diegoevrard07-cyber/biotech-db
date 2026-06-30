"""Normalize CT.gov condition strings to indication categories."""

from __future__ import annotations

import json
import re

import config
from logger import setup_logger

log = setup_logger("indication_taxonomy")

INDICATION_CATEGORIES = [
    "oncology_solid",
    "oncology_heme",
    "cns_neurodegenerative",
    "cns_psychiatric",
    "cns_other",
    "cardiovascular",
    "metabolic",
    "autoimmune",
    "rare_genetic",
    "infectious_disease",
    "ophthalmology",
    "dermatology",
    "respiratory",
    "gi",
    "renal",
    "hematology_nonmalig",
    "other",
]

UNMAPPED_LOG = config.LOGS_DIR / "unmapped_conditions.jsonl"

# Priority order: first match wins
_RULES: list[tuple[str, list[str]]] = [
    ("oncology_heme", [
        r"\bleukemia\b", r"\blymphoma\b", r"\bmyeloma\b", r"\baml\b", r"\ball\b",
        r"\bcll\b", r"\bdlbcl\b", r"\bmyelodysplastic\b", r"\bmds\b",
    ]),
    ("oncology_solid", [
        r"\bcancer\b", r"\bcarcinoma\b", r"\btumor\b", r"\btumour\b", r"\bneoplasm\b",
        r"\bnsclc\b", r"non[- ]small cell lung", r"\bsclc\b", r"small cell lung",
        r"\bbreast cancer\b", r"\bprostate cancer\b", r"\bcolorectal\b", r"\bmelanoma\b",
        r"\bpancreatic\b", r"\bovarian\b", r"\bhepatocellular\b", r"\bhcc\b",
        r"\bglioblastoma\b", r"\bgbm\b", r"\brenal cell\b", r"\brcc\b",
        r"\bbladder cancer\b", r"\butohelial\b", r"\bsarcoma\b", r"\bmesothelioma\b",
    ]),
    ("cns_neurodegenerative", [
        r"alzheimer", r"parkinson", r"huntington", r"amyotrophic lateral sclerosis",
        r"\bals\b", r"dementia", r"mild cognitive impairment",
    ]),
    ("cns_psychiatric", [
        r"depression", r"depressive", r"bipolar", r"schizophren", r"anxiety",
        r"ptsd", r"autism", r"adhd", r"psychiatric",
    ]),
    ("cns_other", [
        r"\bepilep", r"\bseizure\b", r"\bmultiple sclerosis\b", r"\bms\b",
        r"\bmigraine\b", r"\bneuropath", r"\bstroke\b", r"\btbi\b",
    ]),
    ("cardiovascular", [
        r"\bhypertension\b", r"\bheart failure\b", r"\bcardiac\b", r"\bcardiovascular\b",
        r"\batrial fibrillation\b", r"\bafib\b", r"\bhyperlipidemia\b", r"\batherosclero",
        r"\bmyocardial\b", r"\bangina\b", r"\bpad\b", r"peripheral arterial",
    ]),
    ("metabolic", [
        r"diabetes", r"\bt2d\b", r"type 2 diabetes", r"\bobesity\b", r"\bnash\b",
        r"\bnafld\b", r"\bmash\b", r"metabolic syndrome", r"\bhyperglycemia\b",
    ]),
    ("autoimmune", [
        r"\blupus\b", r"\bra\b", r"rheumatoid", r"\bpsoriasis\b", r"\bibd\b",
        r"crohn", r"ulcerative colitis", r"\bms\b", r"autoimmune", r"\bvasculitis\b",
        r"\bsjogren", r"\bscleroderma\b",
    ]),
    ("rare_genetic", [
        r"cystic fibrosis", r"\bcf\b", r"duchenne", r"\bdmd\b", r"fabry",
        r"gaucher", r"pompe", r"hunter syndrome", r"rare disease", r"orphan",
        r"sickle cell", r"thalassemia", r"hemophilia",
    ]),
    ("infectious_disease", [
        r"\bhiv\b", r"\baids\b", r"\bhepatitis\b", r"\bhbv\b", r"\bhcv\b",
        r"\bcovid\b", r"\binfluenza\b", r"\btuberculosis\b", r"\bmalaria\b",
        r"\binfection\b", r"antibacterial", r"antiviral", r"\bvaccine\b",
    ]),
    ("ophthalmology", [
        r"\bmacular degeneration\b", r"\bamd\b", r"\bglaucoma\b", r"\bretina\b",
        r"\buveitis\b", r"\bdiabetic retinopathy\b", r"\bdry eye\b",
    ]),
    ("dermatology", [
        r"\bacne\b", r"\beczema\b", r"\batopic dermatitis\b", r"\bvitiligo\b",
        r"\brosacea\b", r"\bdermatitis\b",
    ]),
    ("respiratory", [
        r"\basthma\b", r"\bcopd\b", r"\bpulmonary\b", r"\bild\b", r"interstitial lung",
        r"\bcystic fibrosis\b", r"\brsv\b",
    ]),
    ("gi", [
        r"\bibs\b", r"irritable bowel", r"\bgastric\b", r"\bgastro\b", r"\bceliac\b",
        r"\bhepatic\b", r"\bliver disease\b", r"\bpancreatitis\b",
    ]),
    ("renal", [
        r"\bckd\b", r"chronic kidney", r"\brenal\b", r"\bnephropathy\b",
        r"\biga nephropathy\b", r"\bfsgs\b",
    ]),
    ("hematology_nonmalig", [
        r"\banemia\b", r"\bthrombocytopenia\b", r"\bneutropenia\b", r"\bhemoglobin\b",
    ]),
]


def _log_unmapped(conditions: list[str]) -> None:
    UNMAPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with UNMAPPED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"conditions": conditions}) + "\n")


def categorize_indication(conditions: list[str] | None) -> str:
    """Return normalized indication_category for a list of CT.gov conditions."""
    if not conditions:
        _log_unmapped([])
        return "other"

    text_blob = " | ".join(c.lower() for c in conditions if c)
    for category, patterns in _RULES:
        for pat in patterns:
            if re.search(pat, text_blob, re.IGNORECASE):
                return category

    _log_unmapped(conditions)
    return "other"


ONCOLOGY_CNS_CATEGORIES = frozenset({
    "oncology_solid", "oncology_heme",
    "cns_neurodegenerative", "cns_psychiatric", "cns_other",
})


def is_gbm_focused(conditions: list[str] | None) -> bool:
    """True if any condition string matches a GBM/high-grade-glioma marker.

    Drives companies.is_gbm_focused so the flagship vertical stays queryable
    inside the broadened oncology/CNS universe.
    """
    if not conditions:
        return False
    blob = " | ".join(c.lower() for c in conditions if c)
    return any(sub in blob for sub in config.GBM_FLAG_SUBSTRINGS)


def is_in_scope(category: str) -> bool:
    """True if an indication_category belongs to the small-cap oncology/CNS universe."""
    return category in ONCOLOGY_CNS_CATEGORIES
