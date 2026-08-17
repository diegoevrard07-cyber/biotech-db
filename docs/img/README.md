# Images

`README.md` references two screenshots of the redesigned Edge Terminal. They are
intentionally not committed by automation — they must reflect the live dashboard.

To capture them:

```bash
streamlit run scripts/terminal.py --server.port 8520
# open http://localhost:8520
```

1. **`docs/img/terminal.png`** — the **Now** page (landing view): the metric strip,
   the action-book blotter, and the right-hand trust rail in one frame.
2. **`docs/img/dashboard.png`** — the **Track record** page: equity vs XBI chart and
   the open-positions table.

Full-window screenshots at 1440px+ width read best on GitHub.
