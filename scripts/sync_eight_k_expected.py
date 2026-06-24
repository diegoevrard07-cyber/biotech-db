"""Sync expected.json event fields from parser output (manual notes preserved)."""

from __future__ import annotations

import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers.layer4.eight_k_parser import parse_8k

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eight_k"


def main() -> None:
    for html_path in sorted(FIXTURES.rglob("*.html")):
        expected_path = html_path.with_suffix(".expected.json")
        if not expected_path.exists():
            continue
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        events = parse_8k(html_path.read_text(encoding="utf-8"), items=expected.get("items"))
        if expected.get("should_match"):
            et = expected["event_type"]
            matching = [e for e in events if e.event_type == et]
            if matching:
                e = matching[0]
                expected["event_date"] = str(e.event_date) if e.event_date else None
                expected["confidence"] = e.confidence
                if e.drug_name:
                    expected["drug_name"] = e.drug_name
        expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
        status = "OK" if (not expected.get("should_match") or matching) else "MISS"
        print(f"{status} {html_path.relative_to(FIXTURES)}")


if __name__ == "__main__":
    main()
