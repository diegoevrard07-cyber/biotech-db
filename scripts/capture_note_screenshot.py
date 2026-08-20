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

# A string only the fully rendered note contains (the masthead title).
READY_TEXT = "Biotech Catalyst Edge Engine"


def capture(*, port: int, out_dir: Path, width: int = 1440, height: int = 900) -> None:
    """Screenshot the note at viewport size (hero) and full-page (complete note)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://localhost:{port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle", timeout=90_000)
        page.wait_for_selector(f"text={READY_TEXT}", timeout=90_000)
        # charts render after the DOM settles; give Plotly a moment
        page.wait_for_timeout(6_000)
        page.screenshot(path=str(out_dir / "terminal.png"))
        page.screenshot(path=str(out_dir / "note_full.png"), full_page=True)
        browser.close()
    print(f"wrote {out_dir / 'terminal.png'} and {out_dir / 'note_full.png'}")


def main() -> None:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="Screenshot the running Edge Terminal")
    ap.add_argument("--port", type=int, default=8530)
    ap.add_argument("--out", default="docs/img")
    args = ap.parse_args()
    capture(port=args.port, out_dir=Path(args.out))


if __name__ == "__main__":
    main()
