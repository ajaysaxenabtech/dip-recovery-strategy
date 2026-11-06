# Disclaimer

## Not financial advice

This repository contains a **personal research playbook** documenting one individual's
study of short-term mean reversion in Indian sector indices and MCX Silver. Nothing in
this repository — including the PDF/DOCX playbook, the README, the parameter tables,
and the backtest code — constitutes financial, investment, or trading advice, nor a
recommendation to buy or sell any instrument.

## No representation of performance

- All IRR, hit-rate, drawdown and rebound-probability figures are **illustrative**.
  They are derived from aggregate index-level studies and a backtest skeleton that has
  not been validated against a complete, survivorship-bias-free historical dataset.
- **Past performance does not guarantee future results.** Mean-reversion edges are
  regime-dependent and have historically failed during crash cascades (2008, March 2020)
  and structural regime shifts.
- The backtest included here falls back to **synthetic data** when market data sources
  are unavailable. Metrics produced from synthetic data are for pipeline demonstration
  only and are not estimates of live performance.

## Substantial risk of loss

- The strategy as documented uses **leverage** (up to 3×). Losses can exceed your
  initial capital. Leveraged losses can accrue faster than you can react.
- Short-term trading generates significant transaction costs, statutory levies
  (STT, GST, stamp duty) and short-term capital-gains tax, all of which reduce net returns.
- Stop-losses do not protect against **overnight gap risk**; fills can be materially
  worse than the stop level.
- Liquidity in sector-index derivatives can dry up without notice; slippage assumptions
  may not hold.

## You are solely responsible

Before deploying any capital:

1. Read the full playbook (`docs/Dip_Recovery_Strategy_Indian_Sectors.pdf`), especially
   Chapter 18 (Known Risks and Failure Modes) and Chapter 14 (Regime Filter).
2. Consult a SEBI-registered investment adviser and a tax professional regarding your
   specific situation, including the F&O business-income classification.
3. Complete the 90-day phased rollout (Chapter 17) — including paper trading — before
   committing full-size capital.
4. Never risk capital you cannot afford to lose.

## No warranty

The software and documentation are provided "as is", without warranty of any kind.
Use at your own risk.
