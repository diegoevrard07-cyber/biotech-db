"""
Seed the portfolio tracker with PAPER positions for the near-term LONG book.

Lets you practice the full workflow (entries, exit alerts, P&L, close) without
real money. Only LONGS are seeded (buy_the_rumor / hold_through) — fades/shorts
are excluded because the fade edge is weak/unvalidated. Positions are sized at
the de-risked weight (suggested_weight x market-cap haircut) against a paper
sleeve, priced at the latest close, and tagged notes='PAPER'.

Everything writes to the SAME portfolio_account / portfolio_holdings tables the
dashboard uses (remote Supabase), so it shows up live in the Portfolio page.

  python scripts/seed_paper_trades.py --sleeve 10000          # seed
  python scripts/seed_paper_trades.py --reset --sleeve 10000  # wipe PAPER + reseed

Idempotent: skips a ticker that already has an OPEN holding. Use --reset to clear
all PAPER holdings and reset cash first.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from db import get_connection
from layers.portfolio import tracker as pf
from action_sheet import risk_haircut
from logger import setup_logger

log = setup_logger("seed_paper_trades")

LONG_TYPES = ("buy_the_rumor", "hold_through")


def run(*, sleeve: float, days: int, reset: bool) -> None:
    with get_connection() as conn:
        raw = conn.connection
        cur = raw.cursor()
        try:
            cur.execute("INSERT INTO portfolio_account (id, cash_usd) VALUES (1, 0) "
                        "ON CONFLICT (id) DO NOTHING")

            if reset:
                cur.execute("DELETE FROM portfolio_holdings WHERE notes = 'PAPER'")
                print(f"Reset: deleted {cur.rowcount} PAPER holdings.")

            # Refuse to clobber a funded/real account unless resetting.
            cur.execute("SELECT COUNT(*) FROM portfolio_holdings WHERE status='open'")
            open_n = cur.fetchone()[0]
            if open_n > 0 and not reset:
                print(f"Account already has {open_n} open holdings. "
                      f"Use --reset to wipe PAPER positions and reseed. Aborting.")
                return

            cur.execute("UPDATE portfolio_account SET cash_usd=%s, starting_capital_usd=%s, "
                        "updated_at=NOW() WHERE id=1", (sleeve, sleeve))

            cur.execute(f"""
                SELECT co.ticker, co.id AS company_id, c.id AS catalyst_id,
                       c.expected_date, es.trade_type, es.suggested_weight, co.market_cap_usd
                FROM edge_scores es
                JOIN catalysts c ON c.id = es.catalyst_id
                JOIN companies co ON co.id = es.company_id
                WHERE es.trade_type = ANY(%s)
                  AND es.suggested_weight IS NOT NULL AND es.suggested_weight > 0
                  AND c.expected_date BETWEEN CURRENT_DATE AND CURRENT_DATE + {int(days)}
                ORDER BY c.expected_date ASC
            """, (list(LONG_TYPES),))
            picks = cur.fetchall()
            cols = [d[0] for d in cur.description]

            # latest close per ticker
            cur.execute("""
                SELECT DISTINCT ON (ticker) ticker, close
                FROM price_history WHERE close IS NOT NULL
                ORDER BY ticker, date DESC
            """)
            prices = {t: float(c) for t, c in cur.fetchall()}

            seen: set[str] = set()
            added, cash = 0, sleeve
            today = date.today()
            print(f"\n=== Seeding PAPER trades (sleeve ${sleeve:,.0f}, next {days}d) ===")
            print(f"{'TICKER':<7}{'TRADE':<14}{'SHARES':>9}{'PRICE':>9}{'COST$':>9}  EXIT")
            for row in picks:
                r = dict(zip(cols, row))
                t = r["ticker"]
                if t in seen:
                    continue
                price = prices.get(t)
                if not price:
                    print(f"{t:<7}{r['trade_type']:<14}{'— skip: no price (delisted/acquired?)':>40}")
                    seen.add(t)
                    continue
                seen.add(t)
                mult = risk_haircut(float(r["market_cap_usd"]) if r["market_cap_usd"] is not None else None)
                dollars = sleeve * float(r["suggested_weight"]) * mult
                shares = round(dollars / price, 2)
                if shares <= 0:
                    continue
                cost = round(shares * price, 2)
                ped, rule = pf.planned_exit(r["trade_type"], r["expected_date"])
                cur.execute("""
                    INSERT INTO portfolio_holdings
                        (ticker, company_id, catalyst_id, side, trade_type, entry_date,
                         shares, entry_price, cost_basis_usd, planned_exit_rule,
                         planned_exit_date, status, notes)
                    VALUES (%s,%s,%s,'long',%s,%s,%s,%s,%s,%s,%s,'open','PAPER')
                """, (t, r["company_id"], r["catalyst_id"], r["trade_type"], today,
                      shares, price, cost, rule, ped))
                cash -= cost
                added += 1
                exit_str = f"{ped} ({r['trade_type']})" if ped else "manual"
                print(f"{t:<7}{r['trade_type']:<14}{shares:>9.2f}{price:>9.2f}{cost:>9,.0f}  {exit_str}")

            cur.execute("UPDATE portfolio_account SET cash_usd=%s, updated_at=NOW() WHERE id=1", (cash,))
            raw.commit()
        finally:
            cur.close()

    print(f"\nSeeded {added} PAPER long(s). Cash remaining: ${cash:,.0f} of ${sleeve:,.0f} "
          f"(deployed ${sleeve - cash:,.0f}).")
    print("Open the dashboard -> Portfolio to manage them. They are tagged notes='PAPER'.")
    log.info("seed_paper_done", added=added, cash=round(cash, 2), sleeve=sleeve)


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed PAPER positions from the near-term long book")
    ap.add_argument("--sleeve", type=float, default=10_000.0)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--reset", action="store_true", help="wipe existing PAPER holdings first")
    args = ap.parse_args()
    try:
        config.preflight()
        run(sleeve=args.sleeve, days=args.days, reset=args.reset)
    except Exception as exc:  # noqa: BLE001
        log.error("seed_paper_failed", error=str(exc))
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
