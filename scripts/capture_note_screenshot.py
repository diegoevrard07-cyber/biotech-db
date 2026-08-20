"""Capture screenshots of the running Edge Terminal for docs/README.

Used by the build-report workflow: waits for the app to actually render
(Streamlit boots async over websockets, so a naive headless screenshot catches
the loading skeleton), then writes a viewport hero shot and a full-page shot.

  pip install playwright  # uses the system Chrome via channel="chrome"
  streamlit run scripts/terminal.py --server.port 8530 &
  python scripts/capture_note_screenshot.py --port 8530 --out docs/img
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

# Selectors that only exist once the note has fully rendered with data.
READY_TEXT = "Biotech Catalyst Edge Engine"  # masthead title
READY_TABLE = '[data-testid="stDataFrame"]'  # the signals blotter


def capture(*, port: int, out_dir: Path, width: int = 1440, height: int = 900) -> None:
    """Screenshot the note at viewport size (hero) and full-page (complete note)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://localhost:{port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-dev-shm-usage", "--force-device-scale-factor=1"],
        )
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle", timeout=120_000)
        page.wait_for_selector(f"text={READY_TEXT}", timeout=120_000)
        page.wait_for_selector(READY_TABLE, timeout=120_000)
        # let any in-flight rerun settle: wait until nothing is marked stale,
        # then give Plotly charts a moment to draw
        page.wait_for_function(
            "document.querySelectorAll('[data-stale=\"true\"]').length === 0",
            timeout=60_000,
        )
        page.wait_for_timeout(8_000)
        body_len = len(page.inner_text("body"))
        print(f"rendered body text: {body_len} chars")
        page.screenshot(path=str(out_dir / "terminal.png"))
        page.screenshot(path=str(out_dir / "note_full.png"), full_page=True)

        # the Trade thesis tab (second tab) for the README's secondary slot
        try:
            page.click('[data-baseweb="tab"]:has-text("Trade thesis")', timeout=30_000)
            page.wait_for_selector("text=The event", timeout=60_000)
            page.wait_for_function(
                "document.querySelectorAll('[data-stale=\"true\"]').length === 0",
                timeout=60_000,
            )
            page.wait_for_timeout(6_000)
            page.screenshot(path=str(out_dir / "thesis.png"), full_page=True)
            print("thesis tab captured")
        except Exception as exc:  # noqa: BLE001 — diagnostics, never fail the job
            print(f"THESIS CAPTURE FAILED: {type(exc).__name__}")
            print("body text after click:", page.inner_text("body")[:800])
        browser.close()
    print(f"wrote terminal.png, note_full.png in {out_dir}")


def main() -> None:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="Screenshot the running Edge Terminal")
    ap.add_argument("--port", type=int, default=8530)
    ap.add_argument("--out", default="docs/img")
    args = ap.parse_args()
    capture(port=args.port, out_dir=Path(args.out))


if __name__ == "__main__":
    main()
