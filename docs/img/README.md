# Images

`README.md` references screenshots of the Edge Terminal in its "institutional
research note" theme (light, warm paper, burgundy accent — locked in
`.streamlit/config.toml`).

## Automated capture (preferred)

The `Data Inventory & Sample Report` GitHub Actions workflow regenerates these
from the live app on every relevant push:

- `docs/img/terminal.png` — landing view at 1440×900 (masthead, pipeline strip,
  top of the signals blotter)
- `docs/img/note_full.png` — full-length note at 1440×3200 (through coverage)

Download from the latest workflow run's artifacts and commit.

## Manual capture (fallback)

```bash
streamlit run scripts/terminal.py --server.port 8520
# open http://localhost:8520 in Chrome, window ~1440px wide, zoom 100%
```

1. **`docs/img/terminal.png`** — the **Research note** tab, scrolled to top:
   masthead, pipeline strip, and the first rows of CURRENT SIGNALS visible.
2. **`docs/img/note_full.png`** — same tab, full page (use a full-page capture:
   DevTools → Cmd+Shift+P → "Capture full size screenshot").
3. **`docs/img/signals.png`** (optional close-up) — crop the CURRENT SIGNALS table
   so the column headers (Model prob. / Market-implied / Edge vs market) read clearly.
4. **`docs/img/evidence.png`** (optional close-up) — the EVIDENCE section with the
   equity-vs-XBI chart and its verdict caption.

Check legibility by viewing `terminal.png` scaled to ~700px wide (README column
width): the masthead title, pipeline-strip numbers, and blotter columns must all
be readable.
