"""
Composite edge scorer + decision layer (Rung 2).

Core score stays simple and base-rate-anchored (few factors, near-equal weights -
the lesson from *Noise*; no kitchen-sink regression). On top of it we add a
decision layer that emits a trade_type, model expected move vs market-implied
move, financing/insider tilts, and a Kelly-fractional position size.

Backward compatible: the original keys and ScoreInputs fields are unchanged so
existing callers/tests keep working; new inputs are optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class ScoreInputs:
    """Everything known about one catalyst at decision time.

    Groups the three grade inputs (timing, trial odds, balance sheet), the
    market-pricing inputs (implied move, run-up, short interest), and the
    data-quality flags (SEC confirmation, date confidence) that gate whether a
    signal is tradable at all.
    """

    catalyst_id: int
    company_id: int
    expected_date: date | None
    base_rate: float | None
    runway_months: float | None
    quarterly_burn: float | None
    sec_confirmed: bool = False
    # --- Rung 2 decision inputs (all optional) ---
    implied_move: float | None = None
    run_up_30d: float | None = None
    short_pct_float: float | None = None
    net_insider_buy_usd: float | None = None
    recent_offering: bool = False
    date_confidence: str | None = None
    requires_manual_verification: bool = False


DEFAULT_WEIGHTS = {
    "proximity": 0.25,
    "base_rate": 0.45,
    "financial": 0.30,
}

# Trade types
BUY_THE_RUMOR = "buy_the_rumor"
FADE = "fade"
HOLD_THROUGH = "hold_through"
AVOID = "avoid"


def score_proximity(expected_date: date | None, *, today: date | None = None) -> float:
    """Map days-to-catalyst to [0, 1] — nearer catalysts are more actionable.

    Coarse steps, not a smooth curve: catalyst dates are estimates, so timing
    precision finer than a month would be false precision.
    """
    if not expected_date:
        return 0.3  # no date: below the 1-year step — barely tradable on timing
    today = today or date.today()
    days = (expected_date - today).days
    if days < 0:
        return 0.1  # already passed: no timing value
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.85
    if days <= 180:
        return 0.65
    if days <= 365:
        return 0.45
    return 0.25


def score_base_rate(base_rate: float | None) -> float:
    """Clamp the empirical P(success) to [0, 1]; 0.5 (max uncertainty) when unknown."""
    if base_rate is None:
        return 0.5
    return max(0.0, min(1.0, float(base_rate)))


def score_financial(runway_months: float | None, quarterly_burn: float | None) -> float:
    """Map balance-sheet survivability to [0, 1].

    A company that cannot fund itself to its catalyst cannot realize the edge —
    under ~3 months of runway it is effectively distressed (0.1); self-funding
    (non-positive burn) scores a full 1.0.
    """
    if quarterly_burn is not None and quarterly_burn <= 0:
        return 1.0
    if runway_months is None:
        return 0.4  # unknown balance sheet: below mid — survivability unproven
    if runway_months >= 24:
        return 1.0
    if runway_months >= 12:
        return 0.75
    if runway_months >= 6:
        return 0.5
    if runway_months >= 3:
        return 0.3
    return 0.1


# ---------------------------------------------------------------------------
# Decision layer
# ---------------------------------------------------------------------------
# Decision thresholds. Each is a deliberate, coarse band — implied moves are
# sparse and approximate for small caps, so only disagreements well outside
# estimation noise are actionable. Fine-tuning these would be overfitting
# (the *Noise* lesson the scorer is built on).

# edge_gap bands (model expected move − options-implied move).
EDGE_GAP_OVERPRICED = -0.05  # < −5pp: market pays for a bigger move than justified
EDGE_GAP_STRONGLY_OVERPRICED = -0.10  # < −10pp: paying up for a coin-flip
EDGE_GAP_UNDERPRICED = 0.10  # > +10pp: market underprices the move — own the binary

# Dilution pressure (financing_tilt ≤ 0). −0.15 ≈ runway < 6 months or repeated
# offerings: the company likely cannot wait for its own catalyst.
FIN_TILT_DISTRESS = -0.15
FIN_TILT_MIN_FOR_LONG = -0.10  # milder dilution still tolerable for a long

# 30-day pre-event run-up levels marking crowded hype.
RUNUP_HEATED = 0.50  # +50% in a month: hope is largely priced in
RUNUP_MANIA = 0.75  # +75% on a long-shot trial: classic avoid

# Base-rate bands: <0.25 is a long-shot trial; 0.45/0.55 are decent and strong odds.
BASE_LONG_SHOT = 0.25
BASE_COIN_FLIP = 0.50
BASE_DECENT = 0.45
BASE_STRONG = 0.55
BASE_RUMOR_MIN = 0.35  # below this, even a near-term catalyst isn't worth riding

# Buy-the-rumor needs a near-term catalyst: 0.85 on the proximity step scale
# corresponds to ≤ ~90 days out.
PROXIMITY_RUMOR_MIN = 0.85


def financing_tilt(
    runway_months: float | None,
    quarterly_burn: float | None,
    recent_offering: bool,
) -> float:
    """Negative when dilution is likely before/around the catalyst."""
    if quarterly_burn is not None and quarterly_burn <= 0:
        return 0.05  # self-funding, no dilution pressure
    tilt = 0.0
    if runway_months is not None:
        if runway_months < 6:
            tilt -= 0.15
        elif runway_months < 12:
            tilt -= 0.05
    if recent_offering:
        tilt -= 0.10
    return round(max(-0.25, tilt), 4)


def insider_tilt(net_insider_buy_usd: float | None) -> float:
    """Positive on net open-market buying (the empirically useful signal)."""
    if net_insider_buy_usd is None:
        return 0.0
    if net_insider_buy_usd > 0:
        return 0.08
    return 0.0


def expected_move(base_rate: float | None) -> float:
    """Heuristic model estimate of the catalyst's absolute move magnitude.

    Most uncertain catalysts (base rate near 0.5) imply the largest expected
    moves. This is a heuristic proxy; backtest/calibration refine it from
    realized magnitudes in catalyst_outcomes.
    """
    base = score_base_rate(base_rate)
    uncertainty = 1.0 - 2.0 * abs(base - 0.5)  # 1 at base=0.5, 0 at extremes
    return round(0.20 + 0.40 * uncertainty, 4)


def kelly_weight(prob: float, payoff_ratio: float = 1.0) -> float:
    """Full-Kelly fraction for a binary bet; clamped at 0 below the edge."""
    if payoff_ratio <= 0:
        return 0.0
    f = (prob * (payoff_ratio + 1) - 1) / payoff_ratio
    return max(0.0, f)


def date_is_reliable(
    sec_confirmed: bool,
    date_confidence: str | None,
    requires_manual_verification: bool,
) -> bool:
    """A date we can time a trade on: SEC-confirmed, or medium/high confidence
    and not flagged as a manual-verification stub."""
    if sec_confirmed:
        return True
    if requires_manual_verification:
        return False
    return (date_confidence or "").lower() in ("high", "medium")


def decide_trade(
    *,
    proximity: float,
    base: float,
    fin_tilt: float,
    run_up_30d: float | None,
    edge_gap: float | None,
    date_reliable: bool = True,
) -> str:
    """Pick a long trade or avoid. Fades/shorts are retired (unvalidated edge).

    Former fade conditions (financing-stressed hype, low-base run-up, strong
    overpricing) now return AVOID so they never enter the book.
    """
    run_up = run_up_30d if run_up_30d is not None else 0.0
    # edge_gap = model expected move - market implied move.
    #   < 0  -> market prices a BIGGER move than the model justifies (overpaying)
    #   > 0  -> market prices a SMALLER move than the model expects (underpriced)
    overpriced = edge_gap is not None and edge_gap < EDGE_GAP_OVERPRICED
    strongly_overpriced = edge_gap is not None and edge_gap < EDGE_GAP_STRONGLY_OVERPRICED
    underpriced = edge_gap is not None and edge_gap > EDGE_GAP_UNDERPRICED

    # 1–3. Former fade setups → avoid (shorts/fades removed from the strategy).
    if fin_tilt <= FIN_TILT_DISTRESS and run_up > RUNUP_HEATED:
        return AVOID
    if base < BASE_LONG_SHOT and (run_up > RUNUP_MANIA or overpriced):
        return AVOID
    if strongly_overpriced and base < BASE_COIN_FLIP:
        return AVOID
    # 4. Cheap optionality: market under-pricing a move on decent odds -> own the binary.
    if underpriced and base >= BASE_DECENT and fin_tilt > FIN_TILT_MIN_FOR_LONG:
        return HOLD_THROUGH
    # 5. Near-term catalyst, acceptable odds, financing OK -> ride the run-up.
    #    Buy-the-rumor lives or dies on timing, so it requires a RELIABLE date.
    if (
        proximity >= PROXIMITY_RUMOR_MIN
        and base >= BASE_RUMOR_MIN
        and fin_tilt > FIN_TILT_MIN_FOR_LONG
        and date_reliable
    ):
        return BUY_THE_RUMOR
    # 6. Strong base rate, financing OK, model not out-priced -> take the binary
    if base >= BASE_STRONG and fin_tilt > FIN_TILT_MIN_FOR_LONG and not overpriced:
        return HOLD_THROUGH
    return AVOID


def suggested_weight(
    trade_type: str,
    *,
    base: float,
    proximity: float,
    kelly_fraction: float,
    max_weight: float,
) -> float:
    """Portfolio weight: positive = long. Fades/shorts always size to 0."""
    if trade_type == HOLD_THROUGH:
        w = kelly_fraction * kelly_weight(base)
    elif trade_type == BUY_THE_RUMOR:
        # Event-driven (exit before the print): smaller, base-agnostic, proximity-scaled.
        w = kelly_fraction * 0.5 * proximity
    elif trade_type == FADE:
        # Retired: never emit a short weight, even if a stale fade label exists.
        return 0.0
    else:
        return 0.0
    return round(max(-max_weight, min(max_weight, w)), 4)


def compute_edge_score(
    inputs: ScoreInputs,
    *,
    weights: dict[str, float] | None = None,
    kelly_fraction: float = 0.25,
    max_weight: float = 0.05,
) -> dict[str, Any]:
    """Score one catalyst end-to-end: grade → decision → size.

    Answers the project's central question for a single event: *is the market's
    priced-in move out of line with this catalyst's intrinsic grade, and if so,
    is the gap big enough to act on — and with how much capital?* Returns the
    full record persisted to `edge_scores` (composite grade, confidence, trade
    type, edge gap, tilts, Kelly-capped weight, and a human-readable rationale).
    """
    w = weights or DEFAULT_WEIGHTS
    proximity = score_proximity(inputs.expected_date)
    base = score_base_rate(inputs.base_rate)
    financial = score_financial(inputs.runway_months, inputs.quarterly_burn)

    composite = w["proximity"] * proximity + w["base_rate"] * base + w["financial"] * financial
    if inputs.sec_confirmed:
        composite = min(1.0, composite + 0.03)

    date_reliable = date_is_reliable(
        inputs.sec_confirmed, inputs.date_confidence, inputs.requires_manual_verification
    )

    confidence = 0.5
    if inputs.base_rate is not None:
        confidence += 0.2
    if inputs.runway_months is not None or (
        inputs.quarterly_burn is not None and inputs.quarterly_burn <= 0
    ):
        confidence += 0.2
    if inputs.expected_date:
        confidence += 0.1
    if not date_reliable:
        confidence -= 0.15  # unreliable timing -> lower our trust in this row
    confidence = max(0.0, min(1.0, confidence))

    # --- decision layer ---
    fin_tilt = financing_tilt(inputs.runway_months, inputs.quarterly_burn, inputs.recent_offering)
    ins_tilt = insider_tilt(inputs.net_insider_buy_usd)
    exp_move = expected_move(inputs.base_rate)
    imp_move = inputs.implied_move
    edge_gap = round(exp_move - imp_move, 4) if imp_move is not None else None

    trade_type = decide_trade(
        proximity=proximity,
        base=base,
        fin_tilt=fin_tilt,
        run_up_30d=inputs.run_up_30d,
        edge_gap=edge_gap,
        date_reliable=date_reliable,
    )
    weight = suggested_weight(
        trade_type,
        base=base,
        proximity=proximity,
        kelly_fraction=kelly_fraction,
        max_weight=max_weight,
    )
    # Insider buying nudges long conviction up a touch (still capped).
    if weight > 0 and ins_tilt > 0:
        weight = round(min(max_weight, weight * (1 + ins_tilt)), 4)

    return {
        "catalyst_proximity_score": round(proximity, 4),
        "science_score": None,
        "base_rate_score": round(base, 4),
        "financial_score": round(financial, 4),
        "composite_score": round(composite, 4),
        "confidence": round(confidence, 4),
        "weights_json": w,
        "trade_type": trade_type,
        "expected_move": exp_move,
        "implied_move": imp_move,
        "edge_gap": edge_gap,
        "financing_tilt": fin_tilt,
        "insider_tilt": ins_tilt,
        "suggested_weight": weight,
        "rationale": (
            f"proximity={proximity:.2f} base={base:.2f} financial={financial:.2f} "
            f"-> {trade_type} (w={weight:+.3f}, exp_move={exp_move:.2f}, "
            f"impl_move={imp_move if imp_move is None else round(imp_move,2)}, "
            f"fin_tilt={fin_tilt:+.2f}, insider={ins_tilt:+.2f})"
            + (" sec_confirmed" if inputs.sec_confirmed else "")
        ),
    }
