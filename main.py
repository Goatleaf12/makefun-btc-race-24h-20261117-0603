"""Panic-dip mean-reversion bot for cbBTC/USDC (make.fun bars-replay/v1).

Long-only, one position at a time, $1,000 paper. Two ways in:

Tier A (washout): the close sits at least A_DROP_BPS below the mean of the
last A_MEAN_BARS closes. Buy with all cash. Exits: take-profit, stop-loss,
a trailing stop off the best close since entry, or a hold cap.

Tier B (bounce): the close has recovered B_BOUNCE_BPS off the low of the
last B_LOW_BARS closes while still at least B_BELOW_BPS under the mean of
the last B_MEAN_BARS closes. Buy with half the cash. Tighter exits.

If Tier A fires while a Tier B position is held, the B position is sold at
once and Tier A is bought on the next bar.

Why this shape: one-minute returns are near-unpredictable and a full round
trip costs roughly 26-43 bps in fee, slippage and impact, so frequent
trading bleeds. What pays is the snap-back after a deep washout, and a
small bounce only when the tape is still cheap.

Every decision is a pure function of closes already seen: no clock, no
network, no randomness. Two runs answer byte for byte the same.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000

# Tunable knobs, all in basis points / bars. The replay driver in /tmp can
# override them in-process for a grid search; the shipped values are the
# ones that cleared the dev gate.
#
# Dev window 20261117-0603 is a QUIET range day (closes $63,874-$65,467;
# deepest dip vs 360-bar mean only 97 bps; 1-min return stdev 6.4 bps).
# The old race's thresholds (300 bps Tier A / 100 bps Tier B) NEVER fire
# here, so Tier A is re-anchored to the window's own scale and re-tuned
# by exact local replay. Grid + walk-forward:
#   A 90/80/200 -> +$38.97 (5 trades, max DD 0.8%); halves +$25.64 / +$1.95
#   neighbours 90/80/180 +$35.48, 60/80/200 +$29.67, 60/80/150 +$27.65
#   (plateau, not a knife edge); trailing 50 bps DESTROYS it (-$11.30),
#   so trailing stays OFF. Tier B never fires on dev; kept with moderate
#   params as race-window insurance (bounce-while-cheap, tight exits).
PARAMS = {
    "a_mean_bars": 90,
    "a_drop_bps": 80,
    "a_tp_bps": 200,
    "a_sl_bps": 100,
    "a_trail_bps": 10000,
    "a_hold_bars": 480,
    "b_mean_bars": 240,
    "b_low_bars": 120,
    "b_below_bps": 60,
    "b_bounce_bps": 40,
    "b_tp_bps": 100,
    "b_sl_bps": 80,
    "b_hold_bars": 240,
    "slip_bps": 100,
}


class Sonar:
    """The strategy's memory: closes seen, and the open position, if any."""

    def __init__(self, params=None):
        self.p = dict(PARAMS if params is None else params)
        self.closes = []
        # Open position: {"tier": "A"/"B", "entry": int, "peak": int, "seq": int}
        self.pos = None
        # Tier of the one buy order still awaiting its fill (or None).
        self.outstanding = None
        # Tier A fired while B was held: buy A on the next bar.
        self.pending_a = False

    def _mean(self, n):
        window = self.closes[-n:]
        return sum(window) // len(window)

    def _tier_a_fires(self, close):
        p = self.p
        if len(self.closes) < p["a_mean_bars"]:
            return False
        return close * BPS <= self._mean(p["a_mean_bars"]) * (BPS - p["a_drop_bps"])

    def _tier_b_fires(self, close):
        p = self.p
        if len(self.closes) < p["b_mean_bars"]:
            return False
        low = min(self.closes[-p["b_low_bars"]:])
        return (
            close * BPS >= low * (BPS + p["b_bounce_bps"])
            and close * BPS <= self._mean(p["b_mean_bars"]) * (BPS - p["b_below_bps"])
        )

    def _buy(self, state, tier, seq):
        p = self.p
        cash = state.cash
        notional = cash if tier == "A" else cash // 2
        if notional < 1_000_000:
            return None
        cid = "buy%s-%d" % (tier, seq)
        self.outstanding = tier
        return {
            "side": "buy",
            "client_id": cid,
            "symbol": SYMBOL,
            "notional_quote_units": notional,
            "max_slippage_bps": p["slip_bps"],
        }

    def _sell_all(self, state, seq):
        held = state.positions.get(SYMBOL)
        if held is None or held.qty <= 0:
            return None
        return {
            "side": "sell",
            "client_id": "sell-%d" % seq,
            "symbol": SYMBOL,
            "qty": held.qty,
            "max_slippage_bps": self.p["slip_bps"],
        }

    def _absorb_fills(self, batch):
        """Fold this batch's fills into the position record."""
        for fill in batch.get("fills", []):
            if fill.get("symbol") != SYMBOL:
                continue
            cid = fill.get("client_id", "")
            if fill.get("status") == "filled" and cid.startswith("buy"):
                tier = cid[3]
                self.pos = {
                    "tier": tier,
                    "entry": int(fill["price"]),
                    "peak": int(fill["price"]),
                    "seq": batch["seq"],
                }
                self.outstanding = None
            elif cid.startswith("buy"):
                self.outstanding = None
            elif fill.get("status") == "filled" and cid.startswith("sell"):
                self.pos = None

    def decide(self, state, batch):
        p = self.p
        close = state.close(SYMBOL)
        if close is None:
            return []
        self._absorb_fills(batch)
        self.closes.append(close)
        seq = state.seq

        if self.pos is not None:
            tier = self.pos["tier"]
            entry = self.pos["entry"]
            # Tier A firing while B is held: dump B now, buy A next bar.
            if tier == "B" and self._tier_a_fires(close):
                order = self._sell_all(state, seq)
                self.pending_a = True
                return [order] if order else []
            if close > self.pos["peak"]:
                self.pos["peak"] = close
            if tier == "A":
                exit_now = (
                    close * BPS >= entry * (BPS + p["a_tp_bps"])
                    or close * BPS <= entry * (BPS - p["a_sl_bps"])
                    or close * BPS <= self.pos["peak"] * (BPS - p["a_trail_bps"])
                    or seq - self.pos["seq"] >= p["a_hold_bars"]
                )
            else:
                exit_now = (
                    close * BPS >= entry * (BPS + p["b_tp_bps"])
                    or close * BPS <= entry * (BPS - p["b_sl_bps"])
                    or seq - self.pos["seq"] >= p["b_hold_bars"]
                )
            if exit_now:
                order = self._sell_all(state, seq)
                return [order] if order else []
            return []

        if self.pending_a:
            self.pending_a = False
            order = self._buy(state, "A", seq)
            return [order] if order else []
        if self._tier_a_fires(close):
            order = self._buy(state, "A", seq)
            return [order] if order else []
        if self._tier_b_fires(close):
            order = self._buy(state, "B", seq)
            return [order] if order else []
        return []


_BOT = Sonar()


def on_batch(state, batch):
    return _BOT.decide(state, batch)


makefun_bars.run(on_batch)
