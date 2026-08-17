# Images

`README.md` references two screenshots of the Edge Terminal (the most visually
impressive artifacts of the project). They are intentionally not committed by
automation — they must reflect the live dashboard.

To capture them:

```bash
streamlit run scripts/terminal.py --server.port 8520
# open http://localhost:8520
```

1. **`docs/img/terminal.png`** — the **Cockpit** page (landing view): KPI cards,
   equity curve with the XBI benchmark overlay, allocation panels.
2. **`docs/img/dashboard.png`** — the **Action Desk** page: the ranked trade blotter
   (ticker, trade type, weight, timing) — the Bloomberg-style table.

Full-window screenshots at 1440px+ width read best on GitHub.
