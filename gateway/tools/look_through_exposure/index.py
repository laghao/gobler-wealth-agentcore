# Copyright Gobler. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Look-Through Exposure tool for the Gobler wealth agent.

Decomposes ETFs/funds into underlying constituents, aggregates true exposure
per issuer / sector / region across the whole portfolio, flags hidden
concentration and correlated clusters, and returns a diversification sub-score.

FAST Gateway Lambda contract (see gateway/tools/sample_tool/sample_tool_lambda.py):
  - event: tool arguments passed directly (NOT wrapped in an HTTP body)
  - context.client_context.custom['bedrockAgentCoreToolName']:
        "<targetName>___<tool_name>"  (triple-underscore delimited)
  - return: {"content": [{"type": "text", "text": "..."}]}  OR  {"error": "..."}
"""

import json
import logging
from collections import defaultdict

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOOL_NAME = "look_through_exposure"

# Minimal built-in reference map for common broad-index ETFs. In production this
# would be replaced by a data-provider lookup (e.g. holdings API). Weights are
# illustrative and fractional (sum <= 1.0; remainder treated as "other").
_REFERENCE_CONSTITUENTS = {
    "IVV": [  # iShares Core S&P 500
        {"symbol": "AAPL", "weight": 0.07, "sector": "Technology", "region": "US"},
        {"symbol": "MSFT", "weight": 0.065, "sector": "Technology", "region": "US"},
        {"symbol": "NVDA", "weight": 0.06, "sector": "Technology", "region": "US"},
        {
            "symbol": "AMZN",
            "weight": 0.035,
            "sector": "Consumer Discretionary",
            "region": "US",
        },
        {"symbol": "GOOGL", "weight": 0.04, "sector": "Communication", "region": "US"},
    ],
    "VOO": [  # Vanguard S&P 500 (same underlying index)
        {"symbol": "AAPL", "weight": 0.07, "sector": "Technology", "region": "US"},
        {"symbol": "MSFT", "weight": 0.065, "sector": "Technology", "region": "US"},
        {"symbol": "NVDA", "weight": 0.06, "sector": "Technology", "region": "US"},
        {
            "symbol": "AMZN",
            "weight": 0.035,
            "sector": "Consumer Discretionary",
            "region": "US",
        },
        {"symbol": "GOOGL", "weight": 0.04, "sector": "Communication", "region": "US"},
    ],
}

# Simple correlated clusters for cluster-risk detection (illustrative).
_CORRELATION_CLUSTERS = {
    "US Mega-Cap Tech": {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"},
}


def _normalise_weights(holdings):
    """Return holdings with a `weight` fraction, deriving it from market_value if needed."""
    total_mv = sum(
        h.get("market_value", 0.0)
        for h in holdings
        if h.get("market_value") is not None
    )
    normalised = []
    for h in holdings:
        w = h.get("weight")
        if w is None and total_mv > 0 and h.get("market_value") is not None:
            w = h["market_value"] / total_mv
        normalised.append({**h, "weight": float(w or 0.0)})
    return normalised


def _decompose(holding):
    """Yield (symbol, effective_weight, sector, region) for a single holding."""
    top_weight = holding["weight"]
    htype = holding.get("type", "stock")
    symbol = holding["symbol"].upper()

    if htype in ("etf", "fund"):
        constituents = holding.get("constituents") or _REFERENCE_CONSTITUENTS.get(
            symbol
        )
        if constituents:
            covered = 0.0
            for c in constituents:
                cw = float(c.get("weight", 0.0))
                covered += cw
                yield (
                    c["symbol"].upper(),
                    top_weight * cw,
                    c.get("sector", "Unknown"),
                    c.get("region", "Unknown"),
                )
            # Remainder of the fund we couldn't decompose -> treat as diversified "other"
            if covered < 1.0:
                yield (
                    f"{symbol}:other",
                    top_weight * (1.0 - covered),
                    "Diversified",
                    "Global",
                )
            return
        # Unknown fund: treat as a single diversified bucket
        yield (f"{symbol}:fund", top_weight, "Diversified", "Global")
        return

    if htype == "cash":
        yield ("CASH", top_weight, "Cash", holding.get("region", "Local"))
        return

    yield (
        symbol,
        top_weight,
        holding.get("sector", "Unknown"),
        holding.get("region", "Unknown"),
    )


def analyze_exposure(holdings, concentration_threshold=0.10):
    holdings = _normalise_weights(holdings)

    issuer = defaultdict(float)
    sector = defaultdict(float)
    region = defaultdict(float)

    for h in holdings:
        for sym, w, sec, reg in _decompose(h):
            base = sym.split(":")[0]
            issuer[base] += w
            sector[sec] += w
            region[reg] += w

    total = sum(issuer.values()) or 1.0
    issuer_pct = {k: v / total for k, v in issuer.items()}

    concentration_flags = [
        {"issuer": k, "true_weight": round(v, 4)}
        for k, v in sorted(issuer_pct.items(), key=lambda x: -x[1])
        if v >= concentration_threshold and k != "CASH"
    ]

    # Correlated-cluster risk
    cluster_flags = []
    for name, members in _CORRELATION_CLUSTERS.items():
        cluster_weight = sum(w for s, w in issuer_pct.items() if s in members)
        if cluster_weight >= max(concentration_threshold * 2, 0.20):
            cluster_flags.append(
                {"cluster": name, "combined_weight": round(cluster_weight, 4)}
            )

    # Diversification sub-score (0..100): penalise concentration (HHI-based) and clusters.
    hhi = sum(w * w for s, w in issuer_pct.items() if s != "CASH")
    # HHI ranges ~ (1/N .. 1). Map to score: lower HHI => higher score.
    base_score = max(0.0, min(100.0, (1.0 - hhi) * 100.0))
    penalty = 5.0 * len(concentration_flags) + 8.0 * len(cluster_flags)
    diversification_score = round(max(0.0, base_score - penalty), 1)

    return {
        "diversification_score": diversification_score,
        "true_issuer_exposure": {
            k: round(v, 4) for k, v in sorted(issuer_pct.items(), key=lambda x: -x[1])
        },
        "sector_exposure": {k: round(v / total, 4) for k, v in sector.items()},
        "region_exposure": {k: round(v / total, 4) for k, v in region.items()},
        "concentration_flags": concentration_flags,
        "correlation_flags": cluster_flags,
        "methodology": (
            "ETFs/funds decomposed via provided or reference constituents; true issuer "
            "weights aggregated across positions. Score = (1 - Herfindahl-Hirschman Index) "
            "* 100, minus 5 pts per single-issuer concentration flag and 8 pts per "
            "correlated-cluster flag."
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

        holdings = event.get("holdings")
        if not holdings:
            return {"error": "Missing required argument: holdings"}

        threshold = float(event.get("concentration_threshold", 0.10))
        result = analyze_exposure(holdings, threshold)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    except Exception as e:  # noqa: BLE001
        logger.error("Error processing request: %s", str(e))
        return {"error": f"Internal server error: {str(e)}"}
