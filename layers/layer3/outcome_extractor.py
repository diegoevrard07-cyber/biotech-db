"""
Extract primary endpoint verdict from CT.gov resultsSection.

Limitation: Phase 2 'success' here means primary endpoint met at readout,
NOT advance-to-next-phase. Endpoint-met rates exceed phase-transition rates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import config
from logger import setup_logger

log = setup_logger("outcome_extractor")

UNEXTRACTABLE_LOG = config.LOGS_DIR / "unextractable_outcomes.jsonl"

TRUE_NARRATIVE = re.compile(
    r"(primary endpoint was met|achieved (its |the )?primary endpoint|"
    r"statistically significant improvement|met the primary endpoint|"
    r"primary objective was met|demonstrated statistically significant)",
    re.IGNORECASE,
)
FALSE_NARRATIVE = re.compile(
    r"(did not meet|failed to meet|failed to demonstrate|"
    r"not statistically significant|did not achieve (its |the )?primary endpoint|"
    r"primary endpoint was not met|did not reach statistical significance)",
    re.IGNORECASE,
)


@dataclass
class OutcomeResult:
    primary_outcome_met: bool | None
    primary_outcome_confidence: str
    extraction_method: str
    raw_results: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionStats:
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0

    def record(self, confidence: str) -> None:
        self.total += 1
        if confidence == "high":
            self.high += 1
        elif confidence == "medium":
            self.medium += 1
        else:
            self.low += 1

    def as_dict(self) -> dict[str, float]:
        if self.total == 0:
            return {"total": 0, "high_pct": 0, "any_pct": 0}
        any_conf = self.high + self.medium
        return {
            "total": self.total,
            "high_pct": round(100 * self.high / self.total, 1),
            "medium_pct": round(100 * self.medium / self.total, 1),
            "low_pct": round(100 * self.low / self.total, 1),
            "any_pct": round(100 * any_conf / self.total, 1),
        }


def _log_unextractable(nct_id: str, reason: str) -> None:
    UNEXTRACTABLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with UNEXTRACTABLE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"nct_id": nct_id, "reason": reason}) + "\n")


def _parse_pvalue(val: str | float | None) -> float | None:
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("", "na", "n/a", "ns", "n.s.", "n.s"):
        return None
    if s.startswith(">"):
        try:
            bound = float(s[1:].strip().lstrip("="))
            return bound + 0.001 if bound >= 0.05 else bound
        except ValueError:
            return None
    if s.startswith("<"):
        try:
            return float(s[1:].strip()) / 2
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _ci_verdict(param: str, lo: float, hi: float) -> bool | None:
    param = param.upper()
    if param in ("HAZARD RATIO", "ODDS RATIO", "RELATIVE RISK"):
        if lo > 1 and hi > 1:
            return False
        if lo < 1 and hi < 1:
            return True
    elif param == "MEAN DIFFERENCE":
        if lo > 0 and hi > 0:
            return True
        if lo < 0 and hi < 0:
            return False
    return None


def _primary_outcomes(results_section: dict) -> list[dict]:
    om = results_section.get("outcomeMeasuresModule", {})
    outcomes = om.get("outcomeMeasures", []) or []
    return [o for o in outcomes if (o.get("type") or "").upper() == "PRIMARY"]


def _extract_from_pvalue(outcome: dict) -> OutcomeResult | None:
    analyses = outcome.get("analyses") or []
    for analysis in analyses:
        p = _parse_pvalue(analysis.get("pValue"))
        if p is None:
            continue
        met = p < 0.05
        return OutcomeResult(
            primary_outcome_met=met,
            primary_outcome_confidence="high",
            extraction_method="pvalue",
            raw_results={"pValue": analysis.get("pValue"), "analysis": analysis},
        )
    return None


def _extract_from_ci(outcome: dict) -> OutcomeResult | None:
    default_param = (outcome.get("paramType") or "").upper()
    for analysis in outcome.get("analyses") or []:
        param = (analysis.get("paramType") or default_param or "").upper()
        lower = analysis.get("ciLowerLimit")
        upper = analysis.get("ciUpperLimit")
        if lower is not None and upper is not None:
            try:
                lo, hi = float(lower), float(upper)
            except (TypeError, ValueError):
                lo, hi = None, None
            if lo is not None and hi is not None:
                met = _ci_verdict(param, lo, hi)
                if met is not None:
                    return OutcomeResult(
                        met, "high", "effect_ci", {"lower": lo, "upper": hi, "param": param, "source": "analysis"}
                    )

    param = default_param
    classes = outcome.get("classes") or []
    for cls in classes:
        for cat in cls.get("categories") or []:
            measurements = cat.get("measurements") or []
            for m in measurements:
                lower = m.get("lowerLimit")
                upper = m.get("upperLimit")
                if lower is None or upper is None:
                    continue
                try:
                    lo, hi = float(lower), float(upper)
                except (TypeError, ValueError):
                    continue
                met = _ci_verdict(param, lo, hi)
                if met is not None:
                    return OutcomeResult(
                        met, "high", "effect_ci", {"lower": lo, "upper": hi, "param": param, "source": "measurement"}
                    )
    return None


def _extract_from_narrative(text: str) -> OutcomeResult | None:
    if not text:
        return None
    if TRUE_NARRATIVE.search(text) and not FALSE_NARRATIVE.search(text):
        return OutcomeResult(True, "medium", "narrative", {"snippet": text[:500]})
    if FALSE_NARRATIVE.search(text):
        return OutcomeResult(False, "medium", "narrative", {"snippet": text[:500]})
    return None


def extract_outcome(study: dict) -> OutcomeResult:
    """Determine if primary endpoint was met for a CT.gov study with results."""
    nct_id = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId", "unknown")
    results = study.get("resultsSection") or {}
    primaries = _primary_outcomes(results)

    if not primaries:
        _log_unextractable(nct_id, "no_primary_outcomes")
        return OutcomeResult(None, "low", "unknown", {})

    for outcome in primaries:
        res = _extract_from_pvalue(outcome)
        if res:
            res.raw_results["outcome_title"] = outcome.get("title")
            return res
        res = _extract_from_ci(outcome)
        if res:
            res.raw_results["outcome_title"] = outcome.get("title")
            return res

    blob = " ".join(
        filter(
            None,
            [
                outcome.get("description", "")
                + " "
                + (outcome.get("title") or "")
                + " "
                + " ".join(
                    str(a.get("statisticalComment") or "")
                    + " "
                    + str(a.get("estimateComment") or "")
                    for a in (outcome.get("analyses") or [])
                )
                for outcome in primaries
            ],
        )
    )
    res = _extract_from_narrative(blob)
    if res:
        return res

    _log_unextractable(nct_id, "no_confident_extraction")
    return OutcomeResult(None, "low", "unknown", {"primary_count": len(primaries)})


def normalize_phase(raw: str | None) -> str | None:
    if not raw:
        return None
    parts = {p.strip().upper() for p in raw.replace(",", "/").split("/")}
    if parts <= {"NA"} or parts == {"N/A"}:
        return None
    if "EARLY_PHASE1" in parts and "PHASE1" not in parts:
        parts.add("PHASE1")
    if "PHASE4" in parts:
        return "PHASE4"
    if "PHASE3" in parts:
        return "PHASE3"
    if "PHASE2" in parts:
        return "PHASE2"
    if "PHASE1" in parts:
        return "PHASE1"
    return None
