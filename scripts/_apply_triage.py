"""Apply triage to companies.csv — run once to rebuild seed with aliases column."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "seeds" / "companies.csv"

REMOVE = {"KRTX", "ORPH", "SURF", "HTGM", "TBIO"}

ALIASES: dict[str, str] = {
    "ADAP": "Adaptimmune Limited|Adaptimmune LLC|Adaptimmune",
    "ADCT": "ADC Therapeutics|ADC Therapeutics AG",
    "ALKS": "Alkermes, Inc.|Alkermes Inc|Alkermes plc",
    "ANAB": "AnaptysBio, Inc.|AnaptysBio Inc",
    "ANNX": "Annexon, Inc.|Annexon Biosciences, Inc.",
    "BCAB": "BioAtla, Inc.|BioAtla Inc",
    "BLTE": "Belite Bio, Inc.|Belite Bio Inc",
    "CGON": "CG Oncology, Inc.|CG Oncology Inc",
    "ETNB": "89bio, Inc.|89Bio, Inc.|89bio Inc",
    "GBIO": "Generation Bio Co.|Generation Bio Company|Generation Bio, Inc.",
    "IBRX": "ImmunityBio, Inc.|ImmunityBio Inc|NantKwest, Inc.",
    "IMNM": "Immunome, Inc.|Immunome Inc",
    "LQDA": "Liquidia Corporation|Liquidia Technologies, Inc.|Liquidia Technologies Inc",
    "PCVX": "Vaxcyte, Inc.|Vaxcyte Inc",
    "XBIO": "Xenetic Biosciences, Inc.|Xenetic Biosciences Inc",
}

UNKNOWN_NOTES = {
    "HYFT": "UNKNOWN_VERIFY_MANUALLY",
    "MTVA": "UNKNOWN_VERIFY_MANUALLY",
    "VYND": "UNKNOWN_VERIFY_MANUALLY",
}

HEADER = (
    "# Universe: US-listed clinical-stage biotech, market cap < $5B focus. "
    "Post-triage: 131 companies (5 removed). ctgov_sponsor_aliases = pipe-separated CT.gov search names."
)


def main() -> None:
    rows_out: list[list[str]] = []
    with CSV_PATH.open(encoding="utf-8") as f:
        lines = f.readlines()

    reader_started = False
    for line in lines:
        if line.startswith("#"):
            continue
        if not reader_started:
            reader_started = True
            continue  # skip old header
        parts = next(csv.reader([line]))
        if not parts or parts[0] == "ticker":
            continue
        ticker = parts[0].strip().upper()
        if ticker in REMOVE:
            continue
        # old format: ticker,name,exchange,market_cap_bucket,primary_indication,notes
        name = parts[1]
        exchange = parts[2]
        bucket = parts[3]
        indication = parts[4]
        notes = parts[5] if len(parts) > 5 else ""
        if ticker in UNKNOWN_NOTES:
            notes = UNKNOWN_NOTES[ticker]
        aliases = ALIASES.get(ticker, "")
        rows_out.append([ticker, name, exchange, bucket, indication, aliases, notes])

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        w = csv.writer(f)
        w.writerow(
            ["ticker", "name", "exchange", "market_cap_bucket", "primary_indication", "ctgov_sponsor_aliases", "notes"]
        )
        w.writerows(rows_out)

    print(f"Wrote {len(rows_out)} companies to {CSV_PATH}")


if __name__ == "__main__":
    main()
