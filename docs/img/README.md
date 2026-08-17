# Images

`README.md` references `docs/img/cockpit.png` — a screenshot of the Edge
Terminal Cockpit page (equity curve + XBI benchmark + action book).

To generate it:

```bash
streamlit run scripts/terminal.py --server.port 8520
# open http://localhost:8520, land on the Cockpit page, screenshot it,
# and save as docs/img/cockpit.png
```

(The image is intentionally not committed by automation — it must reflect the
live dashboard.)
