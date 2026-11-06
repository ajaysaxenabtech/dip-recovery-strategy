"""Silver ETF signal research and timestamped Axis Silver FoF paper screening.

Daily ETF NAV is known after close: the historical test therefore buys the
FoF at its next common NAV. Intraday paper screens require a supplied quote.
No synthetic prices, live downloads, predictions, or orders are used.
"""

import argparse
import csv
import json
import math
import statistics
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


IST = timezone(timedelta(hours=5, minutes=30))
RULES = {
    "dip_only": (False, False),
    "dip_rsi": (True, False),
    "dip_rsi_trend": (True, True),
}


def load_nav(path):
    values = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"Date", "Nav"}.issubset(reader.fieldnames):
            raise ValueError(f"{path}: expected Date,Nav columns")
        for row in reader:
            if not row["Date"] or not row["Nav"]:
                continue
            day = date.fromisoformat(row["Date"])
            nav = float(row["Nav"])
            if not math.isfinite(nav) or nav <= 0 or day in values:
                raise ValueError(f"{path}: invalid or duplicate NAV on {day}")
            values[day] = nav
    if not values:
        raise ValueError(f"{path}: no NAV observations")
    return values


def aligned(etf_csv, fof_csv):
    etf, fof = load_nav(etf_csv), load_nav(fof_csv)
    dates = sorted(etf.keys() & fof.keys())
    if len(dates) < 207:
        raise ValueError("at least 207 common NAV dates are required")
    return dates, [etf[d] for d in dates], [fof[d] for d in dates]


def rsi14(prices, index):
    if index < 14:
        raise ValueError("RSI needs 15 prices")
    changes = [prices[j] - prices[j - 1] for j in range(1, index + 1)]
    gain = sum(max(x, 0) for x in changes[:14]) / 14
    loss = sum(max(-x, 0) for x in changes[:14]) / 14
    for change in changes[14:]:
        gain = (13 * gain + max(change, 0)) / 14
        loss = (13 * loss + max(-change, 0)) / 14
    if gain == loss == 0:
        return 50.0
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def screen(prices, index, rule):
    use_rsi, use_trend = RULES[rule]
    dip = prices[index] / prices[index - 1] - 1
    rsi = rsi14(prices, index)
    ma = statistics.mean(prices[index - 199:index + 1])
    checks = {
        "dip_at_least_2_5_pct": dip <= -0.025,
        "rsi_18_to_42": 18 <= rsi <= 42 if use_rsi else True,
        "etf_above_200day_average": prices[index] >= ma if use_trend else True,
    }
    return checks, dip, rsi, ma


def trades(dates, etf, fof, rule, horizon, start, end, friction):
    found = []
    last_exit = -1
    for i in range(199, len(dates) - horizon - 1):
        entry, exit_ = i + 1, i + 1 + horizon
        if not (start <= dates[i] < end and dates[exit_] < end) or i <= last_exit:
            continue
        checks, dip, _, _ = screen(etf, i, rule)
        if not all(checks.values()):
            continue
        days_held = (dates[exit_] - dates[entry]).days
        load = 0.0025 if days_held <= 7 else 0
        gross = fof[exit_] / fof[entry] - 1
        # Load is charged on redemption value; friction is a separate estimate.
        net = (1 + gross) * (1 - load) * (1 - friction) - 1
        found.append({
            "signal_date": dates[i].isoformat(),
            "entry_date": dates[entry].isoformat(),
            "exit_date": dates[exit_].isoformat(),
            "etf_dip_pct": round(100 * dip, 3),
            "gross_pct": round(100 * gross, 3),
            "exit_load_pct": round(100 * load, 3),
            "net_after_assumed_cost_pct": round(100 * net, 3),
        })
        last_exit = exit_
    return found


def metrics(rows):
    if not rows:
        return {"count": 0}
    gross = [r["gross_pct"] for r in rows]
    net = [r["net_after_assumed_cost_pct"] for r in rows]
    return {
        "count": len(rows),
        "mean_gross_pct": round(statistics.mean(gross), 3),
        "mean_net_pct": round(statistics.mean(net), 3),
        "median_net_pct": round(statistics.median(net), 3),
        "net_win_rate_pct": round(100 * sum(x > 0 for x in net) / len(net), 1),
        "worst_net_pct": round(min(net), 3),
    }


def backtest(dates, etf, fof, split, friction):
    if not 0 <= friction < 1:
        raise ValueError("friction must be a fraction in [0, 1)")
    report = {
        "instrument": "Axis Silver ETF -> Axis Silver Fund of Fund",
        "first_common_date": dates[0].isoformat(),
        "last_common_date": dates[-1].isoformat(),
        "common_nav_dates": len(dates),
        "holdout_from": split.isoformat(),
        "assumed_roundtrip_friction_pct": 100 * friction,
        "early_exit_load_pct": 0.25,
        "entry_assumption": "ETF close NAV signal on t; FoF closing NAV on next common day",
        "exit_assumption": "FoF closing NAV after specified holding sessions; no intraday stop execution",
        "rules": {},
    }
    for rule in RULES:
        report["rules"][rule] = {}
        for horizon in (1, 3, 5, 10):
            train = trades(dates, etf, fof, rule, horizon, dates[0], split, friction)
            holdout = trades(dates, etf, fof, rule, horizon, split, date.max, friction)
            report["rules"][rule][str(horizon)] = {
                "train": metrics(train), "holdout": metrics(holdout),
                "holdout_trades": holdout,
            }
    return report


def paper(snapshot, dates, etf, fof, rule="dip_rsi_trend"):
    observed = datetime.fromisoformat(snapshot["observed_at_ist"])
    quoted = datetime.fromisoformat(snapshot["quote_at_ist"])
    if observed.utcoffset() != IST.utcoffset(None) or quoted.utcoffset() != IST.utcoffset(None):
        raise ValueError("timestamps need explicit +05:30 offsets")
    age = (observed - quoted).total_seconds()
    if observed.date() != quoted.date() or observed.time() >= time(14, 30) or not 0 <= age <= 300:
        raise ValueError("same-day quote must be at most five minutes old and observed before 14:30 IST")
    if not dates[-1] < observed.date() or (observed.date() - dates[-1]).days > 7:
        raise ValueError("latest common NAV must precede observation by at most seven calendar days")
    if not str(snapshot.get("source", "")).strip():
        raise ValueError("quote source is required")
    for flag in ("event_clear", "regime_clear", "funds_ready"):
        if type(snapshot.get(flag)) is not bool:
            raise ValueError(f"{flag} must be explicitly true or false")
    indicative = float(snapshot["etf_indicative_nav"])
    if not math.isfinite(indicative) or indicative <= 0:
        raise ValueError("etf_indicative_nav must be positive and finite")
    checks, dip, rsi, ma = screen(etf + [indicative], len(etf), rule)
    checks.update({flag: snapshot[flag] for flag in ("event_clear", "regime_clear", "funds_ready")})
    return {
        "observed_at_ist": snapshot["observed_at_ist"],
        "quote_at_ist": snapshot["quote_at_ist"],
        "source": snapshot["source"],
        "rule": rule,
        "latest_historical_nav_date": dates[-1].isoformat(),
        "indicative_etf_change_pct": round(100 * dip, 3),
        "indicative_rsi14": round(rsi, 2),
        "indicative_etf_ma200": round(ma, 4),
        "checks": checks,
        "status": "PAPER_CANDIDATE" if all(checks.values()) else "NO_PAPER_ENTRY",
        "note": "The FoF order, if separately placed, receives its applicable closing NAV; this quote is not a fill price.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("backtest", "paper"))
    parser.add_argument("--etf-csv", type=Path, required=True)
    parser.add_argument("--fof-csv", type=Path, required=True)
    parser.add_argument("--split", default="2026-01-01", help="holdout start, YYYY-MM-DD")
    parser.add_argument("--friction", type=float, default=0.002, help="assumed roundtrip fraction")
    parser.add_argument("--snapshot", type=Path, help="paper JSON with timestamped indicative ETF NAV")
    parser.add_argument("--rule", choices=tuple(RULES), default="dip_rsi_trend")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dates, etf, fof = aligned(args.etf_csv, args.fof_csv)
    if args.mode == "backtest":
        result = backtest(dates, etf, fof, date.fromisoformat(args.split), args.friction)
    else:
        if not args.snapshot:
            parser.error("paper requires --snapshot")
        with args.snapshot.open(encoding="utf-8") as handle:
            result = paper(json.load(handle), dates, etf, fof, args.rule)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
