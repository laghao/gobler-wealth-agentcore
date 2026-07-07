# Gobler

**A tax-aware personal wealth agent for the German retail investor — one Strands agent, three custom financial tools over an AgentCore Gateway, a human gate on every trade, deployed as a secured full-stack app in your own AWS account.**

Gobler ingests a portfolio of stocks, ETFs, and cash and does the unglamorous core of personal portfolio management: it looks *through* your ETFs to find what you actually own, checks how far you've drifted from your target weights and what a tax-aware rebalance would cost under German §23 EStG holding rules, and watches a growth-stock watchlist for drawdown-triggered tranche buys. It hands back a **Portfolio Health Score out of 100**, a written rationale, and — for anything that would place a trade — a proposal that **always requires your explicit approval** before it goes anywhere near a broker.

It is built on **[Strands Agents](https://strandsagents.com)** for the agent loop and **[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)** for the production scaffolding (Runtime, Gateway, Memory, Identity, Code Interpreter, Observability), fronted by a Cognito-authenticated React app on Amplify Hosting. Nothing leaves your AWS account.

> This project is built on top of AWS's open-source **Fullstack AgentCore Solution Template (FAST)** — the CDK stack, the Cognito/M2M auth wiring, and the React chat UI come from FAST (Apache-2.0). Gobler is what you get when you strip FAST's sample tool and drop in a real, opinionated use case. A detailed architectural write-up will go up on my blog at [oussamabenlagha.de](http://oussamabenlagha.de).

---

## The problem: nobody looks through their ETFs

A typical German retail portfolio on Trade Republic is a couple of broad ETFs plus a handful of conviction single-names — say an S&P 500 tracker and a chunk of NVIDIA. The investor thinks they're diversified. They are not: the tracker is ~6% NVIDIA by weight, so a "60% ETF / 40% NVIDIA" split is really **~66% NVIDIA-correlated tech exposure**. That hidden concentration is invisible unless something decomposes the fund.

Three jobs interlock here, and each pulls in a different kind of work:

- **Look-through exposure** is *set arithmetic* over fund constituents — deterministic, must be exact.
- **Tax-aware rebalancing** is *drift math plus jurisdiction rules* — the German §23 EStG one-year private-sale window changes which lots you should sell first.
- **Opportunistic deployment** is *threshold monitoring* — drawdown bands off all-time highs, sized against cash, with expiry.

Pour all three into one prompt with a general-purpose "code" tool attached and you get the failure mode every agent engineer knows: unreliable tool selection, arithmetic you can't audit, and tax logic buried in a model's head. The fix is to make each job a **typed, deterministic tool** the agent calls — the LLM decides *when* and *why*, the tool does the *math*, and the reasoning stays reviewable.

---

## What you'll build

| # | Capability | AgentCore service | Where |
|---|---|---|---|
| 1 | Reason→act agent loop | Strands `Agent` + `BedrockModel` | `patterns/strands-single-agent/basic_agent.py` |
| 2 | Look-through ETF decomposition + concentration/correlation flags | Gateway + Lambda target | `gateway/tools/look_through_exposure/` |
| 3 | Tax-aware rebalancing with §23 EStG per-lot logic | Gateway + Lambda target | `gateway/tools/tax_rebalancing/` |
| 4 | Drawdown-triggered tranche-buy alerts (−20/−30/−40%) | Gateway + Lambda target | `gateway/tools/opportunistic_deployment/` |
| 5 | Tools exposed over MCP, discovered at runtime | AgentCore Gateway (MCP) | `patterns/strands-single-agent/tools/gateway.py` |
| 6 | Per-tool authorization by identity claim | AgentCore Policy (Cedar) | `gateway/policies/policy.cedar` |
| 7 | M2M token + user-identity claim injection | Cognito + Pre-Token Lambda | `infra-cdk/lambdas/pretoken-v3/` |
| 8 | Sandboxed ad-hoc math | Code Interpreter | `patterns/strands-single-agent/tools/code_interpreter.py` |
| 9 | Short-term + optional long-term memory | AgentCore Memory | `basic_agent.py` (`use_long_term_memory` in config) |
| 10 | Streaming responses to the UI | Runtime SSE | `basic_agent.py` `stream_async` loop |
| 11 | JWT-authenticated React chat UI | Cognito + Amplify Hosting | `frontend/` |
| 12 | Full tracing / dashboards | Observability (OTEL → CloudWatch) | automatic once deployed |

---

## Architecture

```
     "Score my portfolio: 60% IVV, 40% NVDA"
                    │
                    ▼
   ┌───────────────────────────────────────────────┐
   │        React chat UI (Amplify + Cognito)        │
   │        JWT → AgentCore Runtime /invocations     │
   └───────────────────────┬─────────────────────────┘
                           ▼  (SSE stream)
   ┌───────────────────────────────────────────────┐
   │          GOBLER AGENT (Strands, Runtime)         │
   │   system prompt · Code Interpreter · Memory      │
   │   discovers tools over MCP at each turn          │
   └───────────────────────┬─────────────────────────┘
                           ▼  Bearer token (M2M + user claims)
   ┌───────────────────────────────────────────────┐
   │            AgentCore Gateway (MCP)               │
   │   Cedar policy: authorize by "department" claim  │
   └───┬───────────────────┬───────────────────┬─────┘
       ▼                   ▼                   ▼
  look_through_       tax_rebalancing     opportunistic_
  exposure            (§23 EStG,          deployment
  (ETF decomp,        Trade Republic,     (−20/−30/−40%
  concentration)      HITL approval)      bands, HITL)
       │                   │                   │
       └──────── each a Python Lambda ─────────┘

   Cognito Pre-Token Lambda injects `department` into the M2M token
   Observability traces every hop ──▶ CloudWatch
```

The agent never hard-codes its tool list. It connects to the Gateway over MCP on every turn and discovers whatever targets are registered — so adding a fourth tool is a Lambda + a gateway target + a Cedar action, not an agent code change.

---

## Repo layout

```
patterns/strands-single-agent/   The Gobler agent — Strands loop, Gateway MCP client,
                                  Code Interpreter, Memory session manager.
gateway/
  tools/                          One Lambda per tool (index.py + tool_spec.json):
    look_through_exposure/        ETF/fund decomposition + concentration/correlation.
    tax_rebalancing/              Drift + §23 EStG per-lot annotation, Trade Republic format.
    opportunistic_deployment/     Drawdown bands, tranche sizing, expiry, TA markers.
  policies/policy.cedar           Per-tool authorization (single-statement Cedar).
infra-cdk/                        CDK stack: Runtime, Gateway, Memory, Cognito, Amplify.
  lambdas/                        Pre-token, OAuth2 provider, Cedar policy, feedback.
  lib/backend-construct.ts        Where the three tools are wired to the Gateway.
frontend/                         React chat UI (Cognito auth, SSE streaming parser).
scripts/                          deploy-frontend.py, deploy-with-codebuild.py.
test-scripts/                     test-agent.py, test-gateway.py, test-memory.py.
docs/                             Deep-dives inherited from FAST (Gateway, Cedar, Memory…).
```

---

## The three tools

**Look-Through Exposure** — decomposes each ETF/fund into constituents (via a supplied list or a small built-in reference map), aggregates *true* issuer/sector/region weights across the whole portfolio, flags single-issuer concentration and correlated clusters, and returns a diversification sub-score from an HHI calculation. This is the tool that reveals the "you're 66% tech, not 40%" problem.

**Tax-Aware Rebalancing** — computes per-symbol drift versus target weights, and for every sell candidate annotates each tax lot with its German **§23 EStG** holding-period status (the one-year private-sale window) and unrealized gain/loss, ordering loss lots first for harvesting. Output is formatted for **Trade Republic** and carries `requires_approval: true` — Gobler proposes, you dispose.

**Opportunistic Deployment** — watches a list of instruments for drawdown from their all-time high, triggers the deepest satisfied band (−20% / −30% / −40%), sizes a tranche against available cash, attaches an expiry and technical markers (RSI, 200-DMA), and again gates execution behind human approval.

> **Honest scope note.** The analytics ship with *illustrative* reference data — a tiny built-in ETF constituent map and static correlation clusters — so the repo runs end-to-end with zero external dependencies. For real use, wire the tools to a market-data / holdings provider (or pass full constituents and prices in the request). The §23 EStG treatment is simplified and surfaces holding-period status for review; it is **informational, not tax advice**. Gobler is a portfolio *assistant*, not a regulated adviser, and it never executes trades.

---

## Prerequisites

- **Node.js 20+**, **AWS CLI** configured, **AWS CDK CLI**, **Python 3.11+**, and **Docker** running.
- An **AWS account** with **Bedrock model access** for the Claude model in `basic_agent.py`.
- The AgentCore services here are regional — `us-west-2` is a safe default.
- Deploying from a non-ARM machine? Enable Docker buildx/QEMU (AgentCore Runtime is ARM64). See `docs/DEPLOYMENT.md`.

## Setup & deploy

Gobler reads the ambient AWS credential chain. Set your profile and region, then deploy the backend and frontend:

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=us-west-2

# 1. Configure — edit infra-cdk/config.yaml (stack_name_base, optional admin_user_email)

# 2. Deploy the backend (CDK + Docker). ~5–10 min.
cd infra-cdk
npm install
cdk bootstrap            # first time per account/region
cdk deploy
cd ..

# 3. Deploy the frontend (generates aws-exports.json from stack outputs, builds, hosts on Amplify)
python scripts/deploy-frontend.py
```

Prefer no local tooling? Commit your changes and run `python scripts/deploy-with-codebuild.py` (Python + AWS CLI only — the build runs in CodeBuild).

Then create a Cognito user (or use the `admin_user_email` from config), open the printed Amplify URL, sign in, and ask:

> *"Here's my portfolio: 60% IVV (S&P 500 ETF), 40% NVDA. What's my true NVDA exposure and my diversification score?"*

## Verify it

The test scripts read `stack_name_base` from `infra-cdk/config.yaml`. **Pass your profile/region inline** — `uv run` starts a clean environment:

```bash
AWS_PROFILE=your-profile AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 \
  uv run test-scripts/test-agent.py     # interactive chat against the deployed agent
```

Ask *"what tools do you have?"* — Gobler lists the three financial tools plus the Code Interpreter and explains each.

---

## Teardown

```bash
cd infra-cdk
cdk destroy --force     # removes the stack, S3 buckets, and ECR images
```

## Credits & license

Built on AWS's **Fullstack AgentCore Solution Template (FAST)** (Apache-2.0) — see `LICENSE` and `docs/`. The Gobler use case, the three financial tools, the Cedar policy, and this write-up are my own work.

Built by [Oussama Ben Lagha](https://github.com/laghao).
