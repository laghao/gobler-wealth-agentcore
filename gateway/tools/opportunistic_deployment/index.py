# Copyright Gobler. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opportunistic Deployment tool for the Gobler wealth agent.

Evaluates a watchlist for drawdowns from all-time highs and triggers tranche-buy
alerts at -20% / -30% / -40% bands. Sizes each tranche against available cash,
attaches an expiry timestamp and technical-analysis markers (RSI, 200-DMA), and
always requires human-in-the-loop approval before execution.

FAST Gateway Lambda contract: event = args; return {"content":[{"type":"text","text":...}]}.
"""

import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOOL_NAME = "opportunistic_deployment"

DEFAULT_BANDS = [
    {"drawdown": 0.20, "deploy_fraction": 0.25},
    {"drawdown": 0.30, "deploy_fraction": 0.35},
    {"drawdown": 0.40, "deploy_fraction": 0.40},
]


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _ta_markers(item):
    markers = []
    rsi = item.get("rsi_14")
    if rsi is not None:
        if rsi <= 30:
            markers.append(f"RSI {rsi:.0f}: oversold (supportive of a buy).")
        elif rsi >= 70:
            markers.append(f"RSI {rsi:.0f}: overbought (caution).")
        else:
            markers.append(f"RSI {rsi:.0f}: neutral.")
    sma = item.get("sma_200")
    if sma is not None:
        price = item["current_price"]
        rel = "below" if price < sma else "above"
        markers.append(f"Price {rel} 200-DMA ({sma}).")
    return markers


def evaluate_watchlist(watchlist, available_cash, bands, expiry_days, as_of):
    bands = sorted(bands, key=lambda b: b["drawdown"])
    expiry = (as_of + timedelta(days=expiry_days)).isoformat()
    triggered = []

    for item in watchlist:
        ath = float(item["all_time_high"])
        price = float(item["current_price"])
        if ath <= 0:
            continue
        drawdown = max(0.0, (ath - price) / ath)

        # Find the deepest band the current drawdown satisfies.
        active_band = None
        for b in bands:
            if drawdown >= b["drawdown"]:
                active_band = b
        if active_band is None:
            continue

        deploy_amount = round(available_cash * float(active_band["deploy_fraction"]), 2)
        triggered.append(
            {
                "symbol": item["symbol"].upper(),
                "drawdown_from_ath": round(drawdown, 4),
                "band": f"-{int(active_band['drawdown'] * 100)}%",
                "suggested_deploy_amount": deploy_amount,
                "suggested_shares": round(deploy_amount / price, 4) if price > 0 else 0,
                "expires_on": expiry,
                "technical_markers": _ta_markers(item),
                "rationale": (
                    f"{item['symbol'].upper()} is {drawdown:.1%} below its ATH ({ath}), "
                    f"crossing the -{int(active_band['drawdown'] * 100)}% tranche band."
                ),
            }
        )

    # Opportunity-readiness sub-score: reward having triggers with cash available.
    if available_cash <= 0:
        opportunity_score = 0.0
    elif not triggered:
        opportunity_score = 60.0  # nothing on sale; capital preserved
    else:
        opportunity_score = min(100.0, 60.0 + 10.0 * len(triggered))

    return {
        "opportunity_score": round(opportunity_score, 1),
        "available_cash": available_cash,
        "triggered_tranches": triggered,
        "requires_approval": True,
        "methodology": (
            "Drawdown = (ATH - price) / ATH. The deepest satisfied band among "
            "-20%/-30%/-40% sets the deploy fraction of available cash. Each alert carries "
            "an expiry and TA markers (RSI, 200-DMA). No order executes without explicit "
            "human approval. Informational only; not investment advice."
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

        watchlist = event.get("watchlist")
        available_cash = event.get("available_cash")
        if not watchlist or available_cash is None:
            return {"error": "Missing required arguments: watchlist and available_cash"}

        bands = event.get("tranche_bands") or DEFAULT_BANDS
        expiry_days = int(event.get("expiry_days", 5))
        as_of = (
            _parse_date(event["as_of_date"])
            if event.get("as_of_date")
            else date.today()
        )

        result = evaluate_watchlist(
            watchlist, float(available_cash), bands, expiry_days, as_of
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    except Exception as e:  # noqa: BLE001
        logger.error("Error processing request: %s", str(e))
        return {"error": f"Internal server error: {str(e)}"}
