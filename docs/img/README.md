# Images

`README.md` references two screenshots of the Edge Terminal (the most visually
impressive artifacts of the project). Capture them from the live dashboard so
they always reflect the real state of the paper book.

To capture them:

```bash
streamlit run scripts/terminal.py --server.port 8520
# open http://localhost:8520
```

1. **`docs/img/terminal.png`**: the **Cockpit** page (landing view): KPI cards,
   equity curve with the XBI benchmark overlay, allocation panels.
2. **`docs/img/dashboard.png`**: the **Action Desk** page, the ranked trade blotter
   (ticker, trade type, weight, timing), Bloomberg-style.

Optional third: the **Validation** tab under Market & Models (research findings
table, event study, calibration), a good shot for showing the evidence base.

Full-window screenshots at 1440px+ width read best on GitHub.
