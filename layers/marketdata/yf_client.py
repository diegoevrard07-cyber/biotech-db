"""
Thin, rate-limited, retrying wrapper around yfinance.

yfinance is an unofficial scraper (now backed by curl_cffi); it raises a grab-bag
of exceptions and frequently returns empty frames for delisted/illiquid tickers.
Every helper here degrades gracefully: it returns an empty/`None` result rather
than raising, so a single bad ticker never kills a batch job.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd
import yfinance as yf

import config
from logger import setup_logger

log = setup_logger("yf_client")

_MIN_INTERVAL = 1.0 / max(config.YF_MAX_REQUESTS_PER_SEC, 0.1)
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _with_timeout(label: str, fn: Callable, *, timeout: float | None = None):
    """Run fn in a daemon thread with a hard timeout; raise TimeoutError on expiry.

    A timed-out thread is abandoned (daemon -> dies with the process), so one
    hung symbol can never stall or block exit of the whole batch.
    """
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout if timeout is not None else config.YF_CALL_TIMEOUT)
    if t.is_alive():
        log.warning("yf_timeout", label=label)
        raise TimeoutError(f"yfinance call timed out: {label}")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _attempts() -> int:
    return max(1, config.YF_MAX_RETRIES + 1)


def fetch_history(ticker: str, *, lookback_days: int | None = None) -> pd.DataFrame:
    """Return a daily OHLCV DataFrame (may be empty). Never raises."""
    lookback_days = lookback_days or config.PRICE_LOOKBACK_DAYS
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    last_err: Exception | None = None
    for attempt in range(1, _attempts() + 1):
        _throttle()
        try:
            # Run in the main thread (yfinance caches its crumb/session per thread;
            # a new thread per call would re-negotiate and cost ~50s). history()
            # has its own network timeout param.
            df = yf.Ticker(ticker).history(
                start=start, auto_adjust=False, actions=False,
                raise_errors=False, timeout=config.YF_CALL_TIMEOUT,
            )
            if df is not None and not df.empty:
                return df
            # Empty almost always means delisted/invalid symbol; retrying won't
            # help and costs seconds per name. Give up immediately.
            log.warning("yf_empty_history", ticker=ticker, attempt=attempt)
            return pd.DataFrame()
        except Exception as exc:  # noqa: BLE001 - yfinance raises many types
            last_err = exc
            log.warning("yf_history_error", ticker=ticker, attempt=attempt, error=str(exc))
            time.sleep(min(2 ** attempt, 8))
    if last_err:
        log.error("yf_history_failed", ticker=ticker, error=str(last_err))
    return pd.DataFrame()


def fetch_history_batch(tickers: list[str], *, start: str) -> dict[str, pd.DataFrame]:
    """Download many tickers in ONE request via yf.download. Never raises.

    Returns {ticker: OHLCV DataFrame}; tickers with no data are omitted. This is
    the fast path: one batched, threaded HTTP fetch instead of one crumb-bound
    request per ticker (avoids per-ticker rate-limit stalls).
    """
    out: dict[str, pd.DataFrame] = {}
    tickers = [t for t in tickers if t]
    if not tickers:
        return out
    _throttle()
    try:
        # One daemon-thread bound around the whole batch: the crumb is negotiated
        # once inside it, and a throttled/hung chunk is abandoned, not stuck forever.
        data = _with_timeout(
            f"batch:{len(tickers)}",
            lambda: yf.download(
                tickers, start=start, auto_adjust=False, actions=False,
                threads=True, group_by="ticker", progress=False,
                timeout=config.YF_CALL_TIMEOUT,
            ),
            timeout=max(90.0, config.YF_CALL_TIMEOUT * 4),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("yf_batch_error", n=len(tickers), error=str(exc))
        return out
    if data is None or data.empty:
        return out

    if len(tickers) == 1:
        df = data.dropna(how="all")
        if not df.empty:
            out[tickers[0]] = df
        return out

    for t in tickers:
        try:
            sub = data[t].dropna(how="all")
        except (KeyError, TypeError):
            continue
        if sub is not None and not sub.empty:
            out[t] = sub
    return out


def fetch_info(ticker: str) -> dict[str, Any]:
    """Return the yfinance .info dict (may be empty). Never raises."""
    for attempt in range(1, _attempts() + 1):
        _throttle()
        try:
            info = yf.Ticker(ticker).info
            if info:
                return info
            log.warning("yf_empty_info", ticker=ticker, attempt=attempt)
        except Exception as exc:  # noqa: BLE001
            log.warning("yf_info_error", ticker=ticker, attempt=attempt, error=str(exc))
            time.sleep(min(2 ** attempt, 10))
    return {}


def fetch_option_expirations(ticker: str) -> list[str]:
    """Return available option expiration strings (YYYY-MM-DD). Never raises."""
    _throttle()
    try:
        exps = _with_timeout(f"options:{ticker}", lambda: yf.Ticker(ticker).options)
        return list(exps) if exps else []
    except Exception as exc:  # noqa: BLE001
        log.warning("yf_options_error", ticker=ticker, error=str(exc))
        return []


def fetch_option_chain(ticker: str, expiry: str):
    """Return (calls_df, puts_df) for an expiry, or (None, None). Never raises."""
    _throttle()
    try:
        chain = _with_timeout(f"chain:{ticker}", lambda: yf.Ticker(ticker).option_chain(expiry))
        return chain.calls, chain.puts
    except Exception as exc:  # noqa: BLE001
        log.warning("yf_chain_error", ticker=ticker, expiry=expiry, error=str(exc))
        return None, None
