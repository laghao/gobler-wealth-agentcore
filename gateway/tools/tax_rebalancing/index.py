# Copyright Gobler. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tax-Aware Rebalancing tool for the Gobler wealth agent.

Detects drift vs target weights and proposes rebalancing trades that:
  - prefer harvesting losses,
  - annotate each sell lot with German \u00a723 EStG private-sale holding status,
  - are formatted for Trade Republic execution,
  - ALWAYS require human-in-the-loop approval (requires_approval=True).

NOTE ON \u00a723 EStG: For securities, gains are generally taxed under the
Abgeltungsteuer regime regardless of holding period; the one-year \u00a723 private
sale rule is most relevant for other private assets. Gobler still surfaces the
per-lot holding-period status (held_over_one_year) so the user/advisor can apply
the correct treatment. This is informational, not tax advice.

FAST Gateway Lambda contract: event = args; return {"content":[{"type":"text","text":...}]}.
"""

import json
import logging
from datetime import date, datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOOL_NAME = "tax_rebalancing"
ONE_YEAR_DAYS = 365


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _held_over_one_year(acquired, as_of):
    return (as_of - acquired).days >= ONE_YEAR_DAYS


def compute_rebalance(positions, target_weights, drift_threshold, as_of, jurisdiction):
    # Aggregate current market value per symbol and total.
    per_symbol_mv = {}
    for p in positions:
        per_symbol_mv[p["symbol"].upper()] = per_symbol_mv.get(
            p["symbol"].upper(), 0.0
        ) + float(p["market_value"])
    total_mv = sum(per_symbol_mv.values()) or 1.0
    current_weights = {s: mv / total_mv for s, mv in per_symbol_mv.items()}

    # Drift per symbol (union of held + targeted symbols).
    symbols = set(current_weights) | {s.upper() for s in target_weights}
    drifts = []
    for s in symbols:
        cur = current_weights.get(s, 0.0)
        tgt = float(target_weights.get(s, 0.0))
        drift = cur - tgt
        if abs(drift) >= drift_threshold:
            drifts.append(
                {
                    "symbol": s,
                    "current_weight": round(cur, 4),
                    "target_weight": round(tgt, 4),
                    "drift": round(drift, 4),
                    "action": "sell" if drift > 0 else "buy",
                    "value_delta": round(abs(drift) * total_mv, 2),
                }
            )

    # Per-lot tax annotation for sell candidates, ranked to prefer loss harvesting.
    proposed_trades = []
    for d in sorted(drifts, key=lambda x: -x["value_delta"]):
        if d["action"] == "sell":
            lots = [p for p in positions if p["symbol"].upper() == d["symbol"]]
            annotated = []
            for lot in lots:
                gain = float(lot["market_value"]) - float(lot["cost_basis"])
                acquired = _parse_date(lot["acquired_date"])
                annotated.append(
                    {
                        "quantity": lot["quantity"],
                        "unrealized_gain": round(gain, 2),
                        "is_loss": gain < 0,
                        "acquired_date": lot["acquired_date"],
                        "held_over_one_year": _held_over_one_year(acquired, as_of),
                        "para23_note": (
                            "Loss lot \u2014 harvest candidate."
                            if gain < 0
                            else (
                                "Gain lot held \u22651yr (\u00a723 EStG private-sale exemption may apply)."
                                if _held_over_one_year(acquired, as_of)
                                else "Gain lot held <1yr (\u00a723 EStG private-sale window \u2014 gain "
                                "may be taxable if applicable)."
                            )
                        ),
                    }
                )
            # Sort lots: harvest losses first, then long-held gains.
            annotated.sort(
                key=lambda x: (not x["is_loss"], not x["held_over_one_year"])
            )
            proposed_trades.append(
                {
                    "broker": "Trade Republic",
                    "side": "sell",
                    "symbol": d["symbol"],
                    "approx_value": d["value_delta"],
                    "rationale": f"Overweight by {d['drift']:.2%} vs target.",
                    "lots": annotated,
                }
            )
        else:
            proposed_trades.append(
                {
                    "broker": "Trade Republic",
                    "side": "buy",
                    "symbol": d["symbol"],
                    "approx_value": d["value_delta"],
                    "rationale": f"Underweight by {abs(d['drift']):.2%} vs target.",
                }
            )

    max_drift = max((abs(d["drift"]) for d in drifts), default=0.0)
    rebalancing_score = round(max(0.0, min(100.0, (1.0 - max_drift * 4)) * 100.0), 1)

    return {
        "jurisdiction": jurisdiction,
        "as_of_date": as_of.isoformat(),
        "rebalancing_score": rebalancing_score,
        "drifts": drifts,
        "proposed_trades": proposed_trades,
        "requires_approval": True,
        "methodology": (
            "Drift = current weight - target weight per symbol; proposals raised when "
            "|drift| >= threshold. Sell lots ranked to harvest losses first and annotated "
            "with \u00a723 EStG holding-period status. Score = (1 - 4*max_drift)*100, floored at 0. "
            "Informational only; not tax or investment advice. No order is executed without "
            "explicit human approval."
        ),
    }


def _tool_name_from_context(context):
    delimiter = "___"
    raw = context.client_context.custom["bedrockAgentCoreToolName"]
    return raw[raw.index(delimiter) + len(delimiter) :] if delimiter in raw else raw


def handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    try:
        tool_name = _tool_name_from_context(context)
        if tool_name != TOOL_NAME:
            return {
                "error": f"This Lambda only supports '{TOOL_NAME}', received: {tool_name}"
            }

        positions = event.get("positions")
        target_weights = event.get("target_weights")
        if not positions or not target_weights:
            return {"error": "Missing required arguments: positions and target_weights"}

        drift_threshold = float(event.get("drift_threshold", 0.05))
        jurisdiction = event.get("jurisdiction", "DE")
        as_of = (
            _parse_date(event["as_of_date"])
            if event.get("as_of_date")
            else date.today()
        )

        result = compute_rebalance(
            positions, target_weights, drift_threshold, as_of, jurisdiction
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    except Exception as e:  # noqa: BLE001
        logger.error("Error processing request: %s", str(e))
        return {"error": f"Internal server error: {str(e)}"}
