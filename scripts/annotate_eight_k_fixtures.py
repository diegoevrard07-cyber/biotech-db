"""
Apply hand-verified ground truth to 8-K fixture expected.json files.

Run after fetch_eight_k_fixtures.py. Ground truth derived from reading each
primary 8-K document (not auto-generated).
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eight_k"

# Keyed by fixture stem (ticker_accession without dashes)
GROUND_TRUTH: dict[str, dict] = {
    # --- pdufa_assigned ---
    "LPCN_000110465920028911": {
        "should_match": True,
        "event_type": "pdufa_assigned",
        "event_date": "2020-08-28",
        "drug_name": "TLANDO",
        "confidence": "high",
        "notes": "Press release announcing TLANDO PDUFA Date of August 28, 2020",
    },
    "SWTX_000110465923067823": {
        "should_match": True,
        "event_type": "pdufa_delayed",
        "event_date": None,
        "drug_name": "nirogacestat",
        "confidence": "medium",
        "notes": "PDUFA action date updated/extended; new date not in truncated excerpt",
    },
    "MIRM_000119312523257711": {
        "should_match": True,
        "event_type": "pdufa_assigned",
        "event_date": "2024-03-13",
        "drug_name": "Livmarli",
        "confidence": "high",
        "notes": "PDUFA date assignment for BAL1157-004 sNDA",
    },
    "UNK_000155714221000027": {
        "should_match": True,
        "event_type": "pdufa_delayed",
        "event_date": "2021-08-30",
        "confidence": "high",
        "notes": "PDUFA date extension disclosed",
    },
    # --- crl ---
    "ALDX_000119312526109511": {
        "should_match": True,
        "event_type": "crl",
        "event_date": None,
        "drug_name": "reproxalap",
        "confidence": "medium",
        "notes": "2026 Complete Response Letter for reproxalap NDA; date not in truncated excerpt",
    },
    "ALDX_000119312523283679": {
        "should_match": True,
        "event_type": "crl",
        "event_date": "2023-11-27",
        "drug_name": "reproxalap",
        "confidence": "high",
        "notes": "CRL for reproxalap dry eye NDA",
    },
    "ALDX_000119312525072424": {
        "should_match": True,
        "event_type": "crl",
        "event_date": "2025-04-03",
        "drug_name": "reproxalap",
        "confidence": "high",
        "notes": "CRL resubmission outcome",
    },
    "OMER_000155837021013245": {
        "should_match": True,
        "event_type": "crl",
        "event_date": "2021-10-18",
        "drug_name": "narsoplimab",
        "confidence": "high",
        "notes": "CRL for narsoplimab BLA in HSCT-TMA",
    },
    # --- approval ---
    "ITRM_000095017024117270": {
        "should_match": True,
        "event_type": "approval",
        "event_date": "2024-10-25",
        "drug_name": "ORLYNVAH",
        "confidence": "high",
        "notes": "FDA approved ORLYNVAH for uUTI",
    },
    "REGN_000087258926000014": {
        "should_match": True,
        "event_type": "approval",
        "event_date": None,
        "confidence": "medium",
        "notes": "Dupixent label expansion; approval language without event date in excerpt",
    },
    "NVCR_000164511326000043": {
        "should_match": True,
        "event_type": "approval",
        "event_date": None,
        "confidence": "medium",
        "notes": "Optune/TTF FDA approval reference in 8-K excerpt",
    },
    # --- adcom_scheduled ---
    "ATXI_000110465921129264": {
        "should_match": True,
        "event_type": "adcom_scheduled",
        "confidence": "medium",
        "notes": "AdCom meeting for ATXI product; date not in truncated excerpt",
    },
    "CYTK_000117184322003762": {
        "should_match": True,
        "event_type": "adcom_scheduled",
        "confidence": "medium",
        "notes": "FDA advisory committee date announced",
    },
    "CYTK_000095017025035914": {
        "should_match": True,
        "event_type": "adcom_scheduled",
        "confidence": "medium",
        "notes": "Advisory committee meeting scheduled",
    },
    "ZVRA_000143774924022362": {
        "should_match": True,
        "event_type": "adcom_scheduled",
        "confidence": "medium",
        "notes": "Advisory committee meeting scheduled",
    },
    # --- offering ---
    "DROR_000121390026023642": {
        "should_match": True,
        "event_type": "offering",
        "confidence": "high",
        "notes": "Public offering announced",
    },
    "DROR_000121390026050937": {
        "should_match": True,
        "event_type": "offering",
        "confidence": "high",
        "notes": "Underwritten offering",
    },
    "DROR_000121390026001784": {
        "should_match": True,
        "event_type": "offering",
        "confidence": "high",
        "notes": "Offering launch",
    },
    # --- license_deal ---
    "ELAB_000121390026014285": {
        "should_match": True,
        "event_type": "license_deal",
        "confidence": "high",
        "notes": "License agreement announced",
    },
    "LPCN_000149315221025636": {
        "should_match": True,
        "event_type": "license_deal",
        "confidence": "high",
        "notes": "Collaboration/license agreement",
    },
    "LNTH_000119312522283698": {
        "should_match": True,
        "event_type": "license_deal",
        "confidence": "high",
        "notes": "Licensing deal disclosed",
    },
    "UNK_000143774924001469": {
        "should_match": True,
        "event_type": "license_deal",
        "confidence": "high",
        "notes": "License agreement in 8-K",
    },
    # --- negatives ---
    "competitor_pdufa_ALDX_000119312523256532": {
        "should_match": False,
        "event_type": "pdufa_assigned",
        "notes": "Forward-looking PDUFA risk language, not an assignment",
    },
    "partner_approval_OGEN_000149315220008008": {
        "should_match": False,
        "event_type": "approval",
        "notes": "Partner drug approved; filer is not the asset owner",
    },
    "historical_crl_ZLSSF_000121390024057175": {
        "should_match": False,
        "event_type": "crl",
        "notes": "Past CRL referenced in narrative, not new receipt",
    },
    "routine_underwriting_UNK_000114036126006990": {
        "should_match": False,
        "event_type": "offering",
        "notes": "Shelf/incorporation by reference, not active offering",
    },
    "expanded_access_PLUR_000121390020010010": {
        "should_match": False,
        "event_type": "approval",
        "notes": "Expanded access mention, not approval milestone",
    },
    "lifted_clinical_hold_ADIL_000121390020032495": {
        "should_match": False,
        "event_type": "crl",
        "notes": "Clinical hold lifted — opposite of CRL",
    },
}


def main() -> None:
    """Merge GROUND_TRUTH labels into each fixture's .expected.json (8-K parser test data)."""
    updated = 0
    for html_path in FIXTURES.rglob("*.html"):
        stem = html_path.stem
        expected_path = html_path.with_suffix(".expected.json")
        if stem not in GROUND_TRUTH:
            print(f"SKIP (no ground truth): {stem}")
            continue
        gt = GROUND_TRUTH[stem]
        existing = {}
        if expected_path.exists():
            existing = json.loads(expected_path.read_text(encoding="utf-8"))
        merged = {**existing, **gt}
        expected_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        updated += 1
        print(f"Updated {expected_path.relative_to(FIXTURES)}")
    print(f"Done: {updated} fixtures annotated")


if __name__ == "__main__":
    main()
