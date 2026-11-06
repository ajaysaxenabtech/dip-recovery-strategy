"""
Dip-Recovery Strategy — Indian Volatile Sectors
Companion backtest skeleton for the strategy playbook.

This file is designed to be readable, modifiable, and re-fit on live data.
It pairs with the document `Dip_Recovery_Strategy_Indian_Sectors.docx`.
Every threshold and parameter in the playbook has a matching constant here.

USAGE
-----
    python3 dip_recovery_backtest.py            # default: full run on all 5 instruments
    python3 dip_recovery_backtest.py --summary  # print metrics table only
    python3 dip_recovery_backtest.py --refresh  # force re-download of price data

DATA
----
    Equity indices:  yfinance (^NSEBANK, ^NSEREALTY, ^NSEPHARMA, ^NSEMETAL)
    India VIX:       yfinance (^INDIAVIX)
    MCX Silver:      yfinance SI=F (COMEX proxy) — replace with broker feed for production
    All data cached to ./_cache/ to avoid repeated downloads.

DISCLAIMER
----------
Personal use only. Not investment advice. Numbers are illustrative.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# Optional: yfinance. If unavailable, the script falls back to a synthetic
# data generator so the rest of the pipeline still runs end-to-end.
try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False
    print("[warn] yfinance not installed; using synthetic data for demonstration.")
    print("       Install with: pip install yfinance")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ──────────────────────────────────────────────────────────────────
# 1. Parameters (mirror the playbook)
# ──────────────────────────────────────────────────────────────────
@dataclass
class SectorConfig:
    name: str
    yf_symbol: str
    dip_threshold: float        # close-to-close drop in %, e.g. -1.5
    rsi_low: float
    rsi_high: float
    tp1: float                  # first tranche take-profit (decimal, e.g. 0.010)
    trail: float                # trailing stop (decimal, e.g. 0.005)
    time_stop: int              # max holding days
    hard_stop: float            # decimal, negative (e.g. -0.025)
    leverage: float
    portfolio_weight: float     # 0-1
    per_trade_size: float       # fraction of total capital (e.g. 0.06)


SECTORS: dict[str, SectorConfig] = {
    "Bank Nifty": SectorConfig(
        "Bank Nifty", "^NSEBANK", -1.5, 25, 45, 0.010, 0.005, 5, -0.025, 2.0, 0.25, 0.06),
    "Nifty Realty": SectorConfig(
        "Nifty Realty", "^NSEREALTY", -2.0, 20, 40, 0.015, 0.007, 5, -0.030, 1.5, 0.20, 0.04),
    "Nifty Pharma": SectorConfig(
        "Nifty Pharma", "^NSEPHARMA", -1.5, 25, 42, 0.009, 0.005, 5, -0.025, 2.0, 0.15, 0.04),
    "Nifty Metal": SectorConfig(
        "Nifty Metal", "^NSEMETAL", -2.0, 20, 42, 0.014, 0.007, 5, -0.030, 2.0, 0.20, 0.05),
    "MCX Silver": SectorConfig(
        "MCX Silver", "SI=F", -2.5, 18, 42, 0.015, 0.008, 5, -0.035, 2.0, 0.20, 0.05),
}

# Regime filter thresholds (Chapter 14)
REGIME_VIX_OFF_THRESHOLD = 25.0    # India VIX > this for 2 sessions = OFF
REGIME_VIX_ON_THRESHOLD  = 22.0    # India VIX < this for 2 sessions = ON
REGIME_TREND_THRESHOLD   = -0.05   # Nifty below 200-DMA by > 5% = OFF

# Backtest assumptions
INITIAL_CAPITAL = 1_000_000        # INR 10 lakh starting capital
BROKERAGE_PCT   = 0.0005           # 0.05% per leg
SLIPPAGE_PCT    = 0.0010           # 0.10% per entry, 0.10% per exit
STATUTORY_PCT   = 0.0003           # STT/GST/stamp/exchange — rough
TAX_RATE        = 0.20             # short-term cap gains (equity); F&O = income tax
START_DATE      = "2014-01-01"
END_DATE        = "2024-12-31"

CACHE_DIR = "./_cache"
REPORT_DIR = "./_reports"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────────
# 2. Data loading
# ──────────────────────────────────────────────────────────────────
def _cache_path(symbol: str) -> str:
    safe = symbol.replace("^", "_").replace("=", "_")
    return os.path.join(CACHE_DIR, f"{safe}.parquet")


def load_price(symbol: str, refresh: bool = False) -> pd.DataFrame:
    """Load daily OHLC for a yfinance symbol, with caching."""
    cp = _cache_path(symbol)
    if not refresh and os.path.exists(cp):
        return pd.read_parquet(cp)
    if not HAS_YF:
        # Special synthetic for VIX — typical India VIX range is 12-30
        if "VIX" in symbol:
            return _synthetic_vix()
        return _synthetic_series(symbol)

    print(f"[data] downloading {symbol} ...")
    df = yf.download(symbol, start=START_DATE, end=END_DATE, progress=False, auto_adjust=True)
    if df is None or df.empty:
        print(f"[warn] no data for {symbol}; using synthetic")
        if "VIX" in symbol:
            return _synthetic_vix()
        return _synthetic_series(symbol)

    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.capitalize)
    df.index = pd.to_datetime(df.index)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.to_parquet(cp)
    return df


def _synthetic_vix() -> pd.DataFrame:
    """Generate a synthetic India VIX series in the typical 12-30 range."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(START_DATE, END_DATE)
    n = len(dates)
    # Mean-reverting series around 17, with occasional spikes
    base = 17.0
    sigma = 2.0
    rets = rng.normal(0, sigma, n)
    series = np.zeros(n)
    series[0] = base
    for i in range(1, n):
        # Mean reversion
        rets[i] = 0.05 * (base - series[i - 1]) + rets[i]
        series[i] = max(10, min(45, series[i - 1] + rets[i]))
    df = pd.DataFrame({
        "Open": series, "High": series * 1.05, "Low": series * 0.95,
        "Close": series, "Volume": 0,
    }, index=dates)
    df.index.name = "Date"
    return df


def _synthetic_series(symbol: str) -> pd.DataFrame:
    """Generate a synthetic price series for demonstration when yfinance is unavailable."""
    rng = np.random.default_rng(hash(symbol) % 2**32)
    dates = pd.bdate_range(START_DATE, END_DATE)
    n = len(dates)
    # Geometric brownian motion with dip clusters
    mu = 0.0005
    sigma = 0.012
    rets = rng.normal(mu, sigma, n)
    # Inject occasional sharp drops (~ 1 per month)
    dip_idx = rng.choice(n, size=n // 22, replace=False)
    rets[dip_idx] -= rng.uniform(0.015, 0.04, size=len(dip_idx))
    # Bounce-back premium next day
    for i in dip_idx:
        if i + 1 < n:
            rets[i + 1] += rng.uniform(0.005, 0.015)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    df = pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close,
        "Volume": rng.integers(1e6, 1e8, n),
    }, index=dates)
    df.index.name = "Date"
    return df


# ──────────────────────────────────────────────────────────────────
# 3. Indicators
# ──────────────────────────────────────────────────────────────────
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def compute_true_range(df: pd.DataFrame) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


# ──────────────────────────────────────────────────────────────────
# 4. Regime filter (Chapter 14)
# ──────────────────────────────────────────────────────────────────
def compute_regime(nifty_df: pd.DataFrame, vix_series: pd.Series) -> pd.DataFrame:
    """Return a DataFrame indexed by date with regime_on boolean."""
    nifty_close = nifty_df["Close"]
    sma200 = compute_sma(nifty_close, 200)
    pct_below_sma = (nifty_close - sma200) / sma200

    vix_high = (vix_series > REGIME_VIX_OFF_THRESHOLD).rolling(2).apply(
        lambda x: bool(x.all()), raw=False).fillna(False).astype(bool)
    trend_bad = (pct_below_sma < REGIME_TREND_THRESHOLD).fillna(False).astype(bool)

    regime_off = vix_high | trend_bad
    regime_on = ~regime_off
    return pd.DataFrame({"regime_on": regime_on, "vix": vix_series, "pct_below_sma": pct_below_sma})


# ──────────────────────────────────────────────────────────────────
# 5. Signal generation (Chapter 9 — entry filter stack)
# ──────────────────────────────────────────────────────────────────
def generate_signals(df: pd.DataFrame, cfg: SectorConfig,
                     regime_df: pd.DataFrame) -> pd.DataFrame:
    """Apply the 5-layer entry filter; return signals DataFrame."""
    df = df.copy()
    df["ret_pct"] = df["Close"].pct_change() * 100
    df["rsi"] = compute_rsi(df["Close"], 14)
    df["tr"] = compute_true_range(df)
    df["tr_to_avg"] = df["tr"] / df["tr"].rolling(20).mean()

    # Layer 1: regime
    df = df.join(regime_df[["regime_on"]], how="left")
    df["regime_on"] = df["regime_on"].fillna(False)

    # Layer 5: event exclusion — placeholder; populate with calendar in production
    df["event_block"] = False

    # Layer 2: dip bucket
    layer2 = df["ret_pct"] <= cfg.dip_threshold
    # Layer 3: RSI band
    layer3 = df["rsi"].between(cfg.rsi_low, cfg.rsi_high)
    # Layer 4: vol context
    layer4 = df["tr_to_avg"] <= 2.0

    df["signal"] = layer2 & layer3 & layer4 & df["regime_on"] & ~df["event_block"]
    signals = df[df["signal"]].copy()
    signals["sector"] = cfg.name
    return signals


# ──────────────────────────────────────────────────────────────────
# 6. Exit logic (Chapter 10)
# ──────────────────────────────────────────────────────────────────
@dataclass
class TradeResult:
    sector: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    gross_ret: float          # decimal, e.g. 0.012
    net_ret: float            # after costs, before leverage
    levered_ret: float        # after costs and leverage
    mae: float                # max adverse excursion (decimal, negative)
    mfe: float                # max favourable excursion (decimal, positive)
    leverage: float


def apply_exits(signal_row: pd.Series, df: pd.DataFrame, cfg: SectorConfig) -> Optional[TradeResult]:
    """Walk forward from the day after the signal; apply profit ladder, trail, time-stop, hard stop."""
    sig_date = signal_row.name
    forward = df.loc[df.index > sig_date].head(cfg.time_stop + 2)
    if len(forward) < 1:
        return None

    entry_date = forward.index[0]
    entry_price = forward.iloc[0]["Open"]

    # Costs: brokerage + slippage + statutory on entry
    entry_cost = (BROKERAGE_PCT + SLIPPAGE_PCT + STATUTORY_PCT) * entry_price
    # Exit cost (rough, applied at exit price)
    exit_cost_pct = BROKERAGE_PCT + SLIPPAGE_PCT + STATUTORY_PCT

    peak = entry_price
    trough = entry_price
    first_tranche_done = False
    first_tranche_price = None
    exit_date = None
    exit_price = None
    exit_reason = None

    for i in range(len(forward)):
        row = forward.iloc[i]
        date = forward.index[i]
        day = i + 1  # day 1 = entry day

        # Update peak/trough using intraday high/low
        peak = max(peak, row["High"])
        trough = min(trough, row["Low"])

        # Hard stop check (using Low)
        if row["Low"] <= entry_price * (1 + cfg.hard_stop):
            exit_date = date
            exit_price = entry_price * (1 + cfg.hard_stop)
            exit_reason = "hard_stop"
            break

        # First tranche: take-profit
        if not first_tranche_done and row["High"] >= entry_price * (1 + cfg.tp1):
            first_tranche_done = True
            first_tranche_price = entry_price * (1 + cfg.tp1)

        # Second tranche: trailing stop (only after first tranche filled)
        if first_tranche_done:
            trail_stop = peak * (1 - cfg.trail)
            if row["Low"] <= trail_stop:
                exit_date = date
                exit_price = trail_stop
                exit_reason = "trail_stop"
                break

        # Time stop
        if day >= cfg.time_stop:
            exit_date = date
            exit_price = row["Close"]
            exit_reason = "time_stop"
            break

    if exit_date is None:
        # Fall back: exit at last available close
        exit_date = forward.index[-1]
        exit_price = forward.iloc[-1]["Close"]
        exit_reason = "eod"

    # Compute blended exit price (50% at first tranche if filled, 50% at final exit)
    if first_tranche_done and exit_reason != "hard_stop":
        blended_exit = 0.5 * first_tranche_price + 0.5 * exit_price
    else:
        blended_exit = exit_price

    gross_ret = (blended_exit - entry_price) / entry_price
    exit_cost = exit_cost_pct * blended_exit
    net_ret = gross_ret - (entry_cost / entry_price) - (exit_cost / blended_exit)
    levered_ret = net_ret * cfg.leverage

    mae = (trough - entry_price) / entry_price
    mfe = (peak - entry_price) / entry_price

    return TradeResult(
        sector=cfg.name, signal_date=sig_date, entry_date=entry_date,
        entry_price=entry_price, exit_date=exit_date, exit_price=exit_price,
        exit_reason=exit_reason, gross_ret=gross_ret, net_ret=net_ret,
        levered_ret=levered_ret, mae=mae, mfe=mfe, leverage=cfg.leverage,
    )


# ──────────────────────────────────────────────────────────────────
# 7. Backtest engine
# ──────────────────────────────────────────────────────────────────
def run_backtest(refresh: bool = False) -> dict[str, list[TradeResult]]:
    """Run the full backtest across all configured sectors."""
    # Load Nifty 50 for regime filter
    print("[data] loading Nifty 50 and India VIX for regime filter ...")
    nifty_df = load_price("^NSEI", refresh=refresh)
    if "^INDIAVIX" not in [s for s in []]:  # always check
        pass
    try:
        vix_df = load_price("^INDIAVIX", refresh=refresh)
        vix_series = vix_df["Close"]
    except Exception:
        print("[warn] India VIX unavailable; using synthetic VIX")
        vix_series = pd.Series(20, index=nifty_df.index)

    regime_df = compute_regime(nifty_df, vix_series)

    results: dict[str, list[TradeResult]] = {}
    for name, cfg in SECTORS.items():
        print(f"[bt] running {name} ...")
        df = load_price(cfg.yf_symbol, refresh=refresh)
        signals = generate_signals(df, cfg, regime_df)
        trades = []
        for _, sig in signals.iterrows():
            res = apply_exits(sig, df, cfg)
            if res is not None:
                trades.append(res)
        results[name] = trades
        print(f"      signals={len(signals)}  trades={len(trades)}")
    return results


# ──────────────────────────────────────────────────────────────────
# 8. Metrics
# ──────────────────────────────────────────────────────────────────
def compute_metrics(results: dict[str, list[TradeResult]]) -> pd.DataFrame:
    """Compute per-sector metrics; returns DataFrame."""
    rows = []
    for name, trades in results.items():
        if not trades:
            rows.append({
                "sector": name, "trades": 0, "trades_per_yr": 0.0,
                "hit_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "avg_levered": 0.0, "mae_avg": 0.0, "mae_p10": 0.0,
                "leverage": SECTORS[name].leverage, "weight": SECTORS[name].portfolio_weight,
                "approx_irr": 0.0,
            })
            continue
        cfg = SECTORS[name]
        rets = np.array([t.levered_ret for t in trades])
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        mae = np.array([t.mae for t in trades])

        # Approximate annualisation: trades per year × avg levered return × portfolio weight
        # This is a simplification; a true portfolio backtest tracks equity through time.
        n_years = max(1, (max(t.exit_date for t in trades) - min(t.signal_date for t in trades)).days / 365.25)
        trades_per_year = len(trades) / n_years
        avg_levered = rets.mean()
        # Approximate annual IRR contribution from this sector
        annual_irr = avg_levered * trades_per_year * cfg.portfolio_weight

        rows.append({
            "sector": name,
            "trades": len(trades),
            "trades_per_yr": round(trades_per_year, 1),
            "hit_rate": round((rets > 0).mean() * 100, 1),
            "avg_win":  round(wins.mean() * 100, 2) if len(wins) else 0.0,
            "avg_loss": round(losses.mean() * 100, 2) if len(losses) else 0.0,
            "avg_levered": round(avg_levered * 100, 2),
            "mae_avg":  round(mae.mean() * 100, 2),
            "mae_p10":  round(np.percentile(mae, 10) * 100, 2),
            "leverage": cfg.leverage,
            "weight": cfg.portfolio_weight,
            "approx_irr": round(annual_irr * 100, 1),
        })
    return pd.DataFrame(rows)


def compute_combined_irr(metrics_df: pd.DataFrame) -> float:
    """Sum of per-sector approximate IRR contributions."""
    return metrics_df["approx_irr"].sum()


# ──────────────────────────────────────────────────────────────────
# 9. Reporting
# ──────────────────────────────────────────────────────────────────
def print_summary(metrics_df: pd.DataFrame):
    print()
    print("=" * 80)
    print(" DIP-RECOVERY STRATEGY — BACKTEST SUMMARY")
    print("=" * 80)
    if metrics_df.empty:
        print(" No trades generated.")
        return
    print()
    print(metrics_df.to_string(index=False))
    print()
    combined = compute_combined_irr(metrics_df)
    print(f" Combined approximate IRR:  {combined:.1f}%")
    print(f" Target IRR:                25.0%")
    print(f" Status:                    {'✅ MEETS' if combined >= 25 else '⚠ BELOW'} target")
    print()
    print(" Notes:")
    print("  - 'approx_irr' is a simple linear approximation: avg_levered × trades/yr × weight.")
    print("    A true portfolio backtest tracking equity through time is the next step.")
    print("  - Numbers are net of brokerage and slippage but PRE-tax.")
    print("  - Hit rates and MAE numbers should be compared to playbook Chapter 8/12.")
    print("=" * 80)


def save_reports(metrics_df: pd.DataFrame, results: dict[str, list[TradeResult]]):
    """Save metrics CSV and per-sector trade logs."""
    metrics_df.to_csv(os.path.join(REPORT_DIR, "metrics.csv"), index=False)
    all_trades = []
    for name, trades in results.items():
        for t in trades:
            all_trades.append({
                "sector": t.sector, "signal_date": t.signal_date,
                "entry_date": t.entry_date, "entry_price": t.entry_price,
                "exit_date": t.exit_date, "exit_price": t.exit_price,
                "exit_reason": t.exit_reason, "gross_ret": t.gross_ret,
                "net_ret": t.net_ret, "levered_ret": t.levered_ret,
                "mae": t.mae, "mfe": t.mfe, "leverage": t.leverage,
            })
    if all_trades:
        pd.DataFrame(all_trades).to_csv(
            os.path.join(REPORT_DIR, "trade_log.csv"), index=False)
    print(f"[ok] reports saved to {REPORT_DIR}/")


def plot_rebound_step_function(results: dict[str, list[TradeResult]]):
    """Reproduce Figure 1 from the playbook using actual backtest data."""
    fig, ax = plt.subplots(figsize=(9, 4.5), constrained_layout=True)
    # Bucket boundaries (close-to-close drop %, negative)
    bounds = [-0.5, -1.0, -1.5, -2.0, -3.0, -5.0]
    labels = ["-0.5 to -1%", "-1 to -1.5%", "-1.5 to -2%",
              "-2 to -3%", "-3 to -5%", "< -5%"]
    x = np.arange(len(labels))
    width = 0.15

    sectors_with_trades = [(name, trades) for name, trades in results.items() if trades]
    for offset, (name, trades) in enumerate(sectors_with_trades):
        # Use MAE as a proxy bucket key (in production, store signal-day return in TradeResult)
        mae_pcts = [t.mae * 100 for t in trades]
        buckets_p = []
        for i, label in enumerate(labels):
            if i < len(bounds) - 1:
                lo, hi = bounds[i], bounds[i + 1]
                in_bucket = [t for t in trades if lo > t.mae * 100 >= hi]
            else:
                # Last bucket: below the final boundary
                in_bucket = [t for t in trades if t.mae * 100 < bounds[-1]]
            if in_bucket:
                p = sum(1 for t in in_bucket if t.gross_ret > 0) / len(in_bucket) * 100
            else:
                p = 0
            buckets_p.append(p)
        ax.bar(x + offset * width - (len(sectors_with_trades) - 1) * width / 2,
               buckets_p, width, label=name, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9, rotation=10)
    ax.set_ylabel("P(recovery to entry)  %")
    ax.set_xlabel("Max adverse excursion bucket (proxy for drop size)")
    ax.set_title("Empirical Rebound Probability by Drop Bucket (backtest)")
    ax.axhline(50, color="#999", linestyle="--", linewidth=0.8, label="Baseline 50%")
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(0, 100)
    fig.savefig(os.path.join(REPORT_DIR, "empirical_rebound.png"), dpi=150)
    plt.close(fig)
    print(f"[ok] empirical rebound chart saved to {REPORT_DIR}/empirical_rebound.png")


# ──────────────────────────────────────────────────────────────────
# 10. Main
# ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="Force re-download of price data")
    ap.add_argument("--summary", action="store_true", help="Print summary only (no charts)")
    args = ap.parse_args()

    print("[1/4] Running backtest ...")
    results = run_backtest(refresh=args.refresh)

    print("[2/4] Computing metrics ...")
    metrics_df = compute_metrics(results)

    print("[3/4] Saving reports ...")
    save_reports(metrics_df, results)

    print("[4/4] Plotting ...")
    if not args.summary:
        plot_rebound_step_function(results)

    print_summary(metrics_df)


if __name__ == "__main__":
    main()
