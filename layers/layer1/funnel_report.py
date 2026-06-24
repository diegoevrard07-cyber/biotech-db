"""Print and log catalyst filter funnel summaries."""

from __future__ import annotations

from logger import setup_logger

log = setup_logger("catalyst_funnel")


def print_funnel(stats: dict[str, int]) -> None:
    final = stats.get("final_upcoming", 0)
    lines = [
        "\n=== Catalyst Filter Funnel ===",
        f"Raw extracted from trials:        {stats.get('raw_extracted', 0)}",
        f"  - Dropped (date in past):       {stats.get('dropped_date_past', 0)}",
        f"  - Dropped (invalid phase):      {stats.get('dropped_invalid_phase', 0)}",
        f"  - Dropped (dedupe merge):       {stats.get('dropped_dedupe_merge', 0)}",
        f"  - Dropped (no expected_date):   {stats.get('dropped_no_expected_date', 0)}",
        f"Final upcoming catalysts:          {final}",
    ]
    text = "\n".join(lines)
    print(text)
    log.info(
        "catalyst_funnel",
        raw_extracted=stats.get("raw_extracted", 0),
        dropped_date_past=stats.get("dropped_date_past", 0),
        dropped_invalid_phase=stats.get("dropped_invalid_phase", 0),
        dropped_dedupe_merge=stats.get("dropped_dedupe_merge", 0),
        dropped_no_expected_date=stats.get("dropped_no_expected_date", 0),
        final_upcoming=final,
    )
