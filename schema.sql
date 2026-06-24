-- GBM Edge Engine schema (idempotent)

CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    ticker TEXT UNIQUE,
    name TEXT NOT NULL,
    exchange TEXT,
    market_cap_bucket TEXT,
    primary_indication TEXT,
    ctgov_sponsor_aliases TEXT,
    sponsor_aliases TEXT[],
    cik TEXT,
    market_cap_usd NUMERIC,
    is_gbm_focused BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trials (
    id SERIAL PRIMARY KEY,
    nct_id TEXT UNIQUE NOT NULL,
    company_id INTEGER REFERENCES companies(id),
    title TEXT,
    phase TEXT,
    status TEXT,
    indication TEXT,
    intervention TEXT,
    primary_endpoint TEXT,
    enrollment INTEGER,
    start_date DATE,
    primary_completion_date DATE,
    estimated_readout_date DATE,
    is_randomized BOOLEAN,
    has_control_arm BOOLEAN,
    raw_json JSONB,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS catalysts (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    trial_id INTEGER REFERENCES trials(id),
    catalyst_type TEXT,
    expected_date DATE,
    date_confidence TEXT,
    description TEXT,
    source TEXT,
    source_url TEXT,
    raw_data JSONB,
    requires_manual_verification BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS council_judgments (
    id SERIAL PRIMARY KEY,
    trial_id INTEGER REFERENCES trials(id),
    judge_role TEXT,
    model_name TEXT,
    score INTEGER,
    confidence TEXT,
    reasoning TEXT,
    raw_response JSONB,
    prompt_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trial_scores (
    id SERIAL PRIMARY KEY,
    trial_id INTEGER REFERENCES trials(id) UNIQUE,
    mechanism_class TEXT,
    mechanism_score INTEGER,
    design_score INTEGER,
    skeptic_score INTEGER,
    composite_science_score NUMERIC,
    judge_agreement NUMERIC,
    needs_human_review BOOLEAN DEFAULT FALSE,
    rationale TEXT,
    scored_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS historical_trials (
    id SERIAL PRIMARY KEY,
    nct_id TEXT UNIQUE NOT NULL,
    phase TEXT,
    conditions JSONB,
    indication_category TEXT,
    sponsor_name TEXT,
    sponsor_class TEXT,
    primary_completion_date DATE,
    enrollment INTEGER,
    primary_outcome_met BOOLEAN,
    primary_outcome_confidence TEXT,
    extraction_method TEXT,
    raw_results JSONB,
    source TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS base_rates (
    id SERIAL PRIMARY KEY,
    slice_key TEXT UNIQUE NOT NULL,
    phase TEXT,
    indication_category TEXT,
    sponsor_class TEXT,
    n_trials INTEGER NOT NULL,
    n_successes INTEGER NOT NULL,
    success_rate NUMERIC(5,4),
    ci_low NUMERIC(5,4),
    ci_high NUMERIC(5,4),
    confidence_tier TEXT,
    computed_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sec_filings (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    accession_number TEXT UNIQUE,
    filing_type TEXT,
    filing_date DATE,
    period_of_report DATE,
    url TEXT,
    raw_json JSONB,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS financials (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    filing_id INTEGER REFERENCES sec_filings(id),
    period_end DATE,
    cash_and_equivalents_usd NUMERIC,
    short_term_investments_usd NUMERIC,
    total_liquidity_usd NUMERIC,
    quarterly_opex_usd NUMERIC,
    quarterly_burn_usd NUMERIC,
    runway_months NUMERIC,
    shares_outstanding NUMERIC,
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, period_end)
);

CREATE TABLE IF NOT EXISTS edge_scores (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    catalyst_id INTEGER REFERENCES catalysts(id) UNIQUE,
    catalyst_proximity_score NUMERIC,
    science_score NUMERIC,
    base_rate_score NUMERIC,
    financial_score NUMERIC,
    composite_score NUMERIC,
    confidence NUMERIC,
    weights_json JSONB,
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_trials_company ON trials(company_id);
CREATE INDEX IF NOT EXISTS idx_trials_status ON trials(status);
CREATE INDEX IF NOT EXISTS idx_catalysts_date ON catalysts(expected_date);
CREATE INDEX IF NOT EXISTS idx_council_trial ON council_judgments(trial_id);
CREATE INDEX IF NOT EXISTS idx_edge_composite ON edge_scores(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_financials_company ON financials(company_id);

CREATE TABLE IF NOT EXISTS score_history (
    id SERIAL PRIMARY KEY,
    catalyst_id INTEGER NOT NULL REFERENCES catalysts(id) ON DELETE CASCADE,
    composite_score NUMERIC(4,2) NOT NULL,
    layer1_score NUMERIC(4,2),
    layer2_score NUMERIC(4,2),
    layer3_score NUMERIC(4,2),
    layer4_score NUMERIC(4,2),
    layer_breakdown JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_score_history_catalyst_time
    ON score_history(catalyst_id, computed_at DESC);

ALTER TABLE companies ADD COLUMN IF NOT EXISTS exchange TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS market_cap_bucket TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_indication TEXT;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS raw_data JSONB;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS requires_manual_verification BOOLEAN DEFAULT FALSE;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ctgov_sponsor_aliases TEXT;

CREATE TABLE IF NOT EXISTS fda_approvals (
    id SERIAL PRIMARY KEY,
    application_number TEXT UNIQUE,
    sponsor_name TEXT,
    drug_name TEXT,
    approval_date DATE,
    indication TEXT,
    indication_category TEXT,
    submission_type TEXT,
    is_novel BOOLEAN,
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hist_phase_indication ON historical_trials(phase, indication_category);
CREATE INDEX IF NOT EXISTS idx_hist_sponsor_class ON historical_trials(sponsor_class);
CREATE INDEX IF NOT EXISTS idx_fda_indication ON fda_approvals(indication_category);
CREATE INDEX IF NOT EXISTS idx_fda_date ON fda_approvals(approval_date);
CREATE INDEX IF NOT EXISTS idx_base_rates_lookup ON base_rates(phase, indication_category, sponsor_class);

ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate NUMERIC(5,4);
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate_n INTEGER;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate_ci_low NUMERIC(5,4);
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate_ci_high NUMERIC(5,4);
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate_slice_key TEXT;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS base_rate_source TEXT;
ALTER TABLE base_rates ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'computed';

ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS sec_confirmed BOOLEAN DEFAULT FALSE;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS sec_source_accession TEXT;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS expected_date_original DATE;
ALTER TABLE catalysts ADD COLUMN IF NOT EXISTS expected_date_history JSONB DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS material_events (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    ticker TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    filing_date DATE,
    event_type TEXT NOT NULL,
    event_date DATE,
    confidence TEXT NOT NULL,
    drug_name TEXT,
    extracted_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (accession_number, event_type, event_date)
);

CREATE INDEX IF NOT EXISTS idx_material_events_ticker ON material_events(ticker);
CREATE INDEX IF NOT EXISTS idx_material_events_type ON material_events(event_type);
CREATE INDEX IF NOT EXISTS idx_material_events_filed ON material_events(filing_date);

-- ===========================================================================
-- Rung 2 migration: price, positioning, insider, outcomes, decision scoring
-- All additive and idempotent. No drops.
-- ===========================================================================

-- Daily OHLCV price history (yfinance). Also stores benchmark rows (company_id NULL).
CREATE TABLE IF NOT EXISTS price_history (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    adj_close NUMERIC,
    volume BIGINT,
    source TEXT DEFAULT 'yfinance',
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_price_history_company_date ON price_history(company_id, date);
CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date ON price_history(ticker, date);

-- Objective market positioning / sentiment snapshot.
CREATE TABLE IF NOT EXISTS positioning (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    short_interest NUMERIC,
    short_pct_float NUMERIC,
    days_to_cover NUMERIC,
    implied_move_pct NUMERIC,
    atm_iv NUMERIC,
    option_expiry DATE,
    run_up_30d NUMERIC,
    source TEXT DEFAULT 'yfinance',
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, date)
);
CREATE INDEX IF NOT EXISTS idx_positioning_company_date ON positioning(company_id, date);

-- Insider transactions parsed from SEC Form 4.
CREATE TABLE IF NOT EXISTS insider_transactions (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    cik TEXT,
    accession_number TEXT,
    filing_date DATE,
    transaction_date DATE,
    insider_name TEXT,
    insider_role TEXT,
    transaction_code TEXT,
    shares NUMERIC,
    price_per_share NUMERIC,
    value_usd NUMERIC,
    is_purchase BOOLEAN,
    source TEXT DEFAULT 'sec_form4',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (accession_number, insider_name, transaction_date, transaction_code, shares)
);
CREATE INDEX IF NOT EXISTS idx_insider_company ON insider_transactions(company_id);
CREATE INDEX IF NOT EXISTS idx_insider_filing_date ON insider_transactions(filing_date);

-- Labeled catalyst outcomes (the durable asset / y-variable).
CREATE TABLE IF NOT EXISTS catalyst_outcomes (
    id SERIAL PRIMARY KEY,
    catalyst_id INTEGER NOT NULL REFERENCES catalysts(id) ON DELETE CASCADE,
    company_id INTEGER REFERENCES companies(id),
    resolved_date DATE,
    outcome_label TEXT,
    pre_event_price NUMERIC,
    post_event_price NUMERIC,
    raw_return NUMERIC,
    benchmark_return NUMERIC,
    abnormal_return NUMERIC,
    event_window_days INTEGER,
    source TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (catalyst_id)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_outcomes_company ON catalyst_outcomes(company_id);

-- Calibration run snapshots (optional history of proof-of-edge metrics).
CREATE TABLE IF NOT EXISTS calibration_runs (
    id SERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ DEFAULT NOW(),
    n_pairs INTEGER,
    brier_score NUMERIC,
    model_hit_rate NUMERIC,
    base_rate_hit_rate NUMERIC,
    reliability_json JSONB,
    notes TEXT
);

-- Decision-scoring columns on edge_scores.
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS trade_type TEXT;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS expected_move NUMERIC;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS implied_move NUMERIC;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS edge_gap NUMERIC;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS financing_tilt NUMERIC;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS insider_tilt NUMERIC;
ALTER TABLE edge_scores ADD COLUMN IF NOT EXISTS suggested_weight NUMERIC;

-- Universe flag for small-cap gating (kept, not deleted, when out of universe).
ALTER TABLE companies ADD COLUMN IF NOT EXISTS in_universe BOOLEAN DEFAULT TRUE;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS indication_category TEXT;

-- =====================================================================
-- Portfolio tracker (the user's REAL positions + cash).
-- NOTE: this persists to the configured DATABASE_URL, which is a REMOTE Supabase
-- Postgres (aws pooler), NOT a local DB. Real-money positions leave this machine.
-- =====================================================================

-- Singleton account row: tracks cash so the dashboard can show $ and % of book.
-- Cash auto-adjusts as positions are opened/closed (see layers/portfolio/tracker.py).
CREATE TABLE IF NOT EXISTS portfolio_account (
    id INTEGER PRIMARY KEY DEFAULT 1,
    cash_usd NUMERIC NOT NULL DEFAULT 0,
    starting_capital_usd NUMERIC,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT portfolio_account_singleton CHECK (id = 1)
);

-- One row per position the user holds (open) or has held (closed).
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(id),
    catalyst_id INTEGER REFERENCES catalysts(id),
    side TEXT NOT NULL DEFAULT 'long',          -- 'long' | 'short'
    trade_type TEXT,                            -- buy_the_rumor | hold_through | fade | manual
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    shares NUMERIC NOT NULL,
    entry_price NUMERIC NOT NULL,
    cost_basis_usd NUMERIC,                     -- shares * entry_price (proceeds if short)
    planned_exit_rule TEXT,                     -- human-readable exit instruction
    planned_exit_date DATE,                     -- when to act (derived from catalyst + rule)
    status TEXT NOT NULL DEFAULT 'open',        -- 'open' | 'closed'
    exit_date DATE,
    exit_price NUMERIC,
    realized_pnl_usd NUMERIC,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_holdings_status ON portfolio_holdings(status);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON portfolio_holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_catalyst ON portfolio_holdings(catalyst_id);

-- =====================================================================
-- Event-return study (the REAL returns validation set).
-- One row per (8-K filing, hold-window). Each 8-K is a market-moving
-- announcement date; we measure the realized abnormal return around it
-- (stock move minus benchmark) plus the pre-event run-up. This is the
-- ground-truth dataset used to test whether our signals (run-up, base
-- rate, event type) actually predict realized profit. source = 8-K.
-- =====================================================================
CREATE TABLE IF NOT EXISTS event_returns (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    ticker TEXT NOT NULL,
    accession_number TEXT,
    filing_date DATE NOT NULL,
    filing_type TEXT,
    event_type TEXT,                 -- joined from material_events when available
    hold_days INTEGER NOT NULL,      -- trading days held after the event
    pre_price NUMERIC,               -- close the trading day BEFORE the filing
    post_price NUMERIC,              -- close hold_days trading days after
    raw_return NUMERIC,              -- post/pre - 1
    benchmark_return NUMERIC,        -- XBI move over same window
    abnormal_return NUMERIC,         -- raw - benchmark (the alpha)
    run_up_30d NUMERIC,              -- stock return over the 30 trading days pre-event
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (accession_number, hold_days)
);
CREATE INDEX IF NOT EXISTS idx_event_returns_company ON event_returns(company_id);
CREATE INDEX IF NOT EXISTS idx_event_returns_date ON event_returns(filing_date);
CREATE INDEX IF NOT EXISTS idx_event_returns_hold ON event_returns(hold_days);

-- =====================================================================
-- Stable identity for ClinicalTrials.gov catalysts so re-ingestion can UPSERT
-- (ON CONFLICT) instead of DELETE+INSERT. The old delete-replace pattern broke
-- once edge_scores/portfolio_holdings/catalyst_outcomes referenced catalysts
-- (FK violation) — and violated the "idempotent upsert" rule. A ctgov catalyst
-- is identified by its trial + catalyst_type. Partial index: only ctgov_v2 rows.
-- =====================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalysts_ctgov
    ON catalysts (trial_id, catalyst_type)
    WHERE source = 'ctgov_v2' AND trial_id IS NOT NULL;
