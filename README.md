# btc-race-24h-20261117-0603 — panic-dip mean-reversion bot

[![make.fun](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/Goatleaf12/makefun-btc-race-24h-20261117-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/Goatleaf12/makefun-btc-race-24h-20261117-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)

Long-only cbBTC/USDC bot for make.fun `btc-race-24h-20261117-0603`
(bars-replay/v1, 1-minute bars, $1,000 paper, 1,440 bars).

Strategy: two-tier panic-dip mean reversion. Tier A buys the whole cash
balance when the close sits far below its long mean (a washout), and exits
on take-profit, stop-loss, a trailing stop, or a hold cap. Tier B buys half
the cash on a bounce off the recent low while still below its shorter mean,
with tighter exits. At most one position at a time; every decision is a pure
function of closes already seen, so two runs are byte-identical.

Run locally against the kit dev window with the replay driver in /tmp
(see the builder's report), or `python3 main.py` under the kit harness.
