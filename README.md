# Parity — Polymarket Opportunity Optimizer

**Live market data. Automatic capital allocation. Independent UP and DOWN paper trading.**

Parity watches Polymarket BTC, ETH and SOL 5-minute Up/Down markets, calculates strict equal-quantity arbitrage entries, and displays a Binance spot-price reference chart. The application is a React frontend, FastAPI backend and MongoDB ledger.

## Locked automatic strategy

All new automatic recommendations use **UP = DOWN = Q**, selecting the **smallest** executable quantity satisfying both **combined fee-inclusive cost >= $1.00** and **guaranteed profit >= $0.12**. The $0.12 comprises an immutable $0.10 floor plus an immutable $0.02 safety margin. Neither value can be changed through settings or relaxed by execution/recovery. Binance data never enters these calculations.

Guaranteed payout is `min(actual UP shares, actual DOWN shares)`; guaranteed profit is that payout minus actual combined debit. Before execution, strict recommendations have equal shares and payout Q. Actual unequal fills retain explicit unmatched exposure and are not classified as strict arbitrage.

The economic envelope caps combined debit at `min(available capital, Q - 0.12)`, allocates headroom once between the two legs, and binds per-leg debit and tick-aligned price ceilings. Slippage may tighten these limits. Execution rechecks actual depth, minimum Q, fees, configuration, freshness, synchronization and all caps. Changed quantities require a fresh quote, not silent resizing. Unexpected nonqualifying actual fills are recorded as `RECONCILIATION_EXCEPTION`; no extra purchase is made to meet minimum spending.

The previous generalized optimizer is retained **only as isolated legacy implementation/tests**, not as an automatic recommendation or new-entry execution path. Existing historical trades are not relabeled. Manual UP/DOWN trades remain separate **directional paper trades, not arbitrage**.

> **Current execution mode: PAPER ONLY.** Data is live, but purchases, fills, balances and recovery are simulated. Real-money orders are locked. There is no wallet connection, private-key handling, signing or live order submission. No ChatGPT/AI integration is included.

## Contents

- [Quick start for users](#quick-start-for-users)
- [The three different prices](#the-three-different-prices)
- [Binance chart and market selection](#binance-chart-and-market-selection)
- [Buy UP or DOWN independently](#buy-up-or-down-independently)
- [Automatic optimizer](#automatic-optimizer)
- [Risk objectives](#risk-objectives)
- [Fees, precision and execution](#fees-precision-and-execution)
- [Positions, activity and settlement](#positions-activity-and-settlement)
- [Why no data or no buy recommendation?](#why-no-data-or-no-buy-recommendation)
- [Settings and paper lab](#settings-and-paper-lab)
- [Application architecture](#application-architecture)
- [Developer setup](#developer-setup)
- [API reference](#api-reference)
- [Tests](#tests)
- [Safety and current limitations](#safety-and-current-limitations)

## Quick start for users

1. Open **Auto optimizer**. Select **BTC**, **ETH** or **SOL** using the market tiles.
2. The chart shows that asset's live **Binance spot price**, its opening price for the current five-minute UTC window, the difference and countdown.
3. The persistent contract-price bar shows **both UP and DOWN share prices at once**, including when you scroll. These prices come from Polymarket, not Binance.
4. To choose your own direction, use the two **Trade your view** order panels. Enter a spend limit or share quantity for UP or DOWN, preview the order, accept the directional risk and confirm the paper buy. Neither panel requires switching a tab.
5. For an automatic strict pair, set your **Budget pool** and optional maximum deployment, then click **Apply parameters**. The equality rule and profit/cost thresholds are locked.
6. Review the automatic recommendation: deployed capital, unspent cash, per-leg costs/fees/shares and both outcome profits. Click **Buy recommended trade** only when a valid opportunity exists.
7. Inspect actual paper fills in **Positions** and **Trade activity**. **Paper lab** supports manual payoff calculations and failure scenarios. **Market data** exposes live ladders and feed checks.

Initial workspace: **$1,000 simulated paper cash**, **$100 allocation pool**, **Strict Arbitrage** strategy. The paper balance and pool are different concepts. Buying does not spend real money.

Download this file inside the app with **User guide** beside the main heading.

## The three different prices

| Value | Source | Meaning |
|---|---|---|
| Binance current price | Binance public spot `aggTrade` stream | Underlying BTC/ETH/SOL price against USDT; reference only |
| Five-minute opening reference | Binance exact current `5m` candle's open | Binance price at the start of the chart's five-minute UTC window |
| UP / DOWN share price | Polymarket CLOB ask ladders | Current contract purchase price, usually displayed in cents; fees are additional |

**Polymarket's official price to beat is not Binance's opening price.** Market settlement uses the market's stated oracle and rules, which may include Chainlink/TWAP. The UI explicitly marks the official reference unavailable when it cannot be verified. Parity never settles a position from the Binance chart or infers official results from spot direction.

## Binance chart and market selection

- The Binance chart uses public, unauthenticated market-data-only endpoints. No Binance API key is required.
- The line is bootstrapped from real one-second candles, then updated using aggregate trades. No fabricated chart history is displayed.
- The dashed **OPEN** line represents the exact Binance candle open for the current five-minute window.
- The chart, candle reference, countdown and selected symbol change automatically when the window rolls over or you select another asset.
- **BINANCE LIVE** means fresh stream data. **STALE** means the last-known price remains visible but freshness is not confirmed. Historical points are not presented as new ticks.
- Binance spot availability and Polymarket book availability are independent. One may be live while the other is recovering.

## Buy UP or DOWN independently

Both panels and their Polymarket share prices remain visible together. These are **manual directional paper orders**, not automatic paired allocations.

### Spend mode

1. Choose **Spend $**.
2. Enter your maximum spend, **including trading fees**, in either panel.
3. Click **Buy UP** or **Buy DOWN**. Parity walks that side's actual ask ladder and rounds shares down to the supported quantity increment.
4. The preview shows executable shares, average price, purchase cost, fee, actual debit, profit if that side wins, maximum possible loss and remaining paper cash.
5. Check the risk-acceptance box and click **Confirm paper buy**.

The actual debit can be below your spend limit because of quantity precision, slippage limits or available depth. An amount too small to meet the exchange minimum is rejected with the minimum-share requirement.

### Shares mode

Select **Shares** and enter the number of shares instead. The exchange minimum and quantity precision still apply. Quotes reject unsupported increments or insufficient liquidity.

### Important manual-order safeguards

- **A single-side order can lose its entire debit.** Strict-arbitrage qualification does **not** apply to manual directional trades. This is displayed before buying and requires explicit confirmation.
- Budget pool, maximum deployment, paper cash, minimum order size, quantity precision, slippage, quote age, book freshness and market rollover checks still apply.
- Each quote lasts **10 seconds**. Refresh it if it expires; changed prices are never silently accepted above the quoted debit/price limit.
- Manual orders use one fresh outcome book. A cross-book synchronization requirement is unnecessary because the order does not combine UP and DOWN. The selected book must still be valid, fresh and connected.
- Pausing the auto optimizer does not disable intentional manual paper trading.
- Manual orders are simulated FOK orders: all requested shares fill, or no shares fill. A deliberate single-side buy becomes **OPEN_POSITION**, not **RECOVERY_REQUIRED**. It is not automatically hedged or unwound.
- Each buy creates a separate ledger record, so UP and DOWN buys can be inspected separately.

## Automatic optimizer

### Budget pool is not trade size

```text
Budget pool = maximum capital available to this trade
Capital deployed = actual UP debit + actual DOWN debit
Unspent cash = budget pool − capital deployed
```

Parity searches the smallest **equal share quantity Q** on the two actual ladders. UP and DOWN dollar costs may differ, but their share counts must be identical. A $100 pool can recommend $0, $6, $20 or another amount. It never forces a trade simply because money is available.

For each candidate it walks both real ask ladders, applies fees per execution price, respects minimum quantities and precision, rejects excessive slippage, and computes both outcomes:

```text
Total debit = UP purchase cost + UP fee + DOWN purchase cost + DOWN fee
Payout if UP wins = UP shares × $1
Payout if DOWN wins = DOWN shares × $1
Profit in an outcome = that outcome's payout − total debit
Worst case = minimum of the two profits
Best case = maximum of the two profits
ROI = outcome profit / total debit × 100%
```

All displayed automatic payoffs are conditional on both legs filling. Two independent orders are **not atomic**. Execution risk and market/oracle risk are not erased by a positive payoff calculation.

### Workflow

1. Choose an asset. Strict arbitrage is the only automatic strategy.
2. Enter the capital pool and optional maximum deployment under **Capital limit**.
3. Click **Apply parameters**. The optimizer recalculates on relevant updates with a one-second fallback.
4. Review **Capital deployed**, **Unspent cash**, the exact two allocations and both outcome profits.
5. Execute only when **READY**. The backend revalidates the current books and approved quantities/debits after simulated latency.

## Risk objectives

There is one active automatic objective: **smallest qualifying Q**, not maximum deployment or maximum profit dollars. No client-set objective, loss limit or minimum-profit override can bypass the locked policy. Generalized settings from existing workspaces are archived during migration; budget, deployment cap and feed/paper safeguards are preserved.

**NO_TRADE is a valid result.** If fees/depth prevent a $0.12 profit at an equal executable quantity costing at least $1.00, the recommendation deploys zero. `CALCULATION_LIMIT` means minimality could not be proven within the bounded search; it also authorizes no order.

## Fees, precision and execution

Fee configuration is queried from each market's CLOB metadata. Supported current exponent-one fee curve:

```text
fee at each fill level = shares × fee rate × price × (1 − price)
```

Fees are accumulated at the actual execution levels and rounded to five decimal places in collateral. They are not an arbitrary flat charge. The rate is not assumed to be constant across all markets. Unsupported fee configurations fail closed.

- **Price tick** constrains prices.
- **Quantity precision** constrains shares and comes from the installed V2 SDK rounding configuration.
- **Minimum order size** is market-specific and comes from CLOB metadata/book snapshots.
- **Slippage limit** bounds the highest ask touched relative to the current best ask; a displayed best ask is not assumed to fill the entire order.

### Paired paper execution and recovery

```text
READY → EXECUTING → LOCKED_POSITION → SETTLED
                 → ABORTED
                 → RECOVERY_REQUIRED → retry → LOCKED_POSITION
                                     → unwind → UNWOUND
                                     → retained exposure if recovery is unsafe
```

Simulated latency allows the actual live order books to change. Liquidity retention and independent leg failures are configurable. A missing leg is retried only within the objective and recovery limits; otherwise the filled side may be sold against live bid depth, with sell fees. If a safe complete unwind cannot be verified, the app leaves the exposure open and says so.

This build uses **FOK per leg**. FAK/partial-fill policies are not implemented. Concurrent two-leg FOK orders still do not form an atomic pair.

## Positions, activity and settlement

- **Positions** shows open automatic pairs, deliberate manual single-side holdings, executing orders and unresolved recovery exposure.
- **Trade activity** includes filled, aborted, unwound and settled records. Expand a row to inspect requested versus actual shares, average execution prices, actual fees, remaining exposure and the event timeline.
- **Export CSV** exports the filtered ledger summary.
- **Re-evaluate recovery** is available for automatic trades requiring recovery. Manual directional positions are not automatically considered failed hedges.
- Actual simulated cash changes only when fills, unwinds or confirmed payouts occur. Repeating an execution request with the same request identifier does not buy twice.
- A settlement worker waits for authoritative Gamma confirmation: closed market, resolved status and exact binary outcome prices. It then credits the winning remaining shares and records realized paper P&L. Merely reaching the countdown's end does not settle a trade.
- Settlement can lag market expiry. Pending positions remain open until final exchange confirmation. Binance prices never decide the result.

## Why no data or no buy recommendation?

The app now distinguishes **no profitable/acceptable trade** from **data that is temporarily unsafe**. It retains last-known prices during reconnection, labels them accordingly and disables unsafe purchases.

| Status | Meaning / response |
|---|---|
| READY | Current automatic allocation satisfies the selected constraints |
| NO_TRADE | Books are usable, but no acceptable allocation beats no trade after fees/limits |
| PAUSED | Automatic recommendations paused; manual orders can still use valid books |
| CONNECTING / UNAVAILABLE | Feed establishing/reconnecting; cached prices are not executable |
| RECOVERING | A book is being validated/rebuilt after an anomaly; no recommendation uses it |
| STALE | Exchange event or local arrival age exceeded the configured freshness limit |
| UNSYNCED | UP and DOWN exchange event times differ beyond the synchronization threshold |
| NO_LIQUIDITY | At least one outcome has no asks; near expiry this is possible |
| ROLLOVER | Transition to the next active five-minute market |

### Troubleshooting

1. Check **Binance status** and **Polymarket feed health** independently.
2. If the app connection is interrupted, use **Reconnect** in the recovery banner. EventSource also reconnects automatically.
3. Open **Market data** to inspect timestamps, local ages, available asks and the reason for the guard.
4. Confirm the optimizer is not paused and the budget can cover both exchange minimum orders.
5. Check the objective: Guaranteed Profit and zero-loss constraints can legitimately produce long no-trade periods.
6. Do not loosen safety limits just to make a buy recommendation appear. Verify the feed first.
7. A manual buy can remain available while the automatic pair is UNSYNCED, because it only consumes one independently verified book.
8. At rollover or an empty side of a market, allow discovery to finish. Never mistake cached quotes for current execution prices.

Reliability handling applies full WebSocket batches before checking the result. A transient crossed/locked book immediately becomes non-executable; it has a short 300 ms normalization window before rebuilding. A persistent anomaly reconnects the current market instead of unnecessarily rediscovering the whole series. Old queued deltas superseded by a full snapshot are ignored and counted. Heartbeat timeouts trigger reconnects. These checks do not fabricate a recommendation.

## Settings and paper lab

Default settings:

| Setting | Default |
|---|---|
| Cross-book event gap | 100 ms |
| Maximum local/exchange book age | 3,000 ms |
| Maximum slippage | 2% |
| Simulated network latency | 350 ms |
| Independent order failure probability | 0.03 (3%) |
| Retained liquidity after latency | 0.95 (95%) |
| Maximum recovery loss | $1 |

All settings persist per browser workspace in MongoDB. Paper lab supports manual two-leg payoff inspection and named stress scenarios: realistic execution, one-sided fill, disappearing liquidity, stale feed, unsynchronized books and order rejection. Stress execution still requires a genuine valid automatic recommendation.

## Application architecture

```text
Gamma discovery → market / condition / UP+DOWN token IDs
              → CLOB REST bootstrap + WebSocket snapshots/deltas
              → local books → integrity checks → integer optimizer
              → SSE → React decision screen

Binance 5m + 1s candle bootstrap → aggregate-trade WebSocket
                               → independent spot reference → SSE → chart

Manual quote OR auto recommendation → backend revalidation
                                   → PAPER execution → MongoDB ledger
                                   → confirmed resolution → paper payout
```

Key files:

| Path | Responsibility |
|---|---|
| `backend/server.py` | FastAPI routes, scoped workspaces, SSE, settlement worker |
| `backend/feed.py` | Polymarket discovery, live stream, rollover and recovery |
| `backend/books.py` | Book state, price/quantity rules, pair and single-side integrity |
| `backend/spot.py` | Binance bootstrap, stream, reference window and chart history |
| `backend/optimizer.py` | HiGHS mixed-integer search and exact Decimal book walking |
| `backend/paper.py` | Automatic paired paper execution and recovery |
| `backend/manual.py` | Independent directional paper quotes and purchases |
| `backend/models.py` | Request/setting validation |
| `frontend/src/components/SpotChart.jsx` | Binance chart and source labels |
| `frontend/src/components/SharePriceBar.jsx` | Persistent UP/DOWN contract quotes |
| `frontend/src/components/ManualTradePanel.jsx` | Side-by-side buys and risk confirmation |
| `frontend/src/components/DecisionPanel.jsx` | Automatic allocation and payoff UI |
| `frontend/src/pages/WorkspacePages.jsx` | Positions, activity, feed, settings, paper lab |
| `memory/PRD.md` | Product history and remaining scope |

Market registry entries configure asset/name/series prefix/duration. Backend feed mechanics are shared rather than one implementation per asset. More assets/durations require corresponding UI and validation updates; this version's active choices are BTC/ETH/SOL at five minutes.

## Developer setup

### Prerequisites

- Python 3.11+
- Node.js and Yarn 1.x compatible with the checked-in frontend lockfile
- MongoDB reachable from the backend
- Network access to the public Polymarket and Binance market-data endpoints

### Configuration

Copy the example configuration files to `.env` only on a fresh checkout. In an existing environment, preserve its configured values.

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Backend required variables:

```text
MONGO_URL                  MongoDB connection URI
DB_NAME                    MongoDB database name
CORS_ORIGINS               Comma-separated allowed frontend origins
POLY_GAMMA_URL             Gamma discovery host
POLY_CLOB_URL              CLOB REST host
POLY_WS_URL                Public market WebSocket endpoint
MARKET_REGISTRY            JSON asset/series configuration
PAPER_INITIAL_BALANCE      Initial simulated cash for a new workspace
BINANCE_REST_URL           Official market-data-only REST host
BINANCE_WS_URL             Official market-data-only combined-stream endpoint
```

Frontend: `REACT_APP_BACKEND_URL` is the backend origin, without `/api`. All product API calls derive from it. Existing environments also configure development WebSocket options.

### Install and run locally

```bash
# Backend: from the repository root
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python -m uvicorn server:app --host 0.0.0.0 --port 8001

# In another terminal, from the repository root
cd frontend
yarn install --frozen-lockfile
yarn start
```

Use the frontend URL printed by the dev server. In the hosted workspace, services are already supervisor-managed; do not launch duplicate servers. Regular edits hot-reload. Environment/dependency changes require the corresponding supervised service restart.

Build the frontend with `cd frontend && yarn build`.

MongoDB stores `sessions` and `trades`. Initial paper cash applies only when creating a new workspace. Financial data does not reset when the page reloads. Clearing browser storage creates a different workspace identifier; this app does not implement user login or cross-device account recovery.

### GitHub / secrets

Keep `.env`, credentials, signing keys, database passwords and local virtual environments out of Git. Commit `.env.example` instead. This build needs **no API keys** for its public feeds. Do not add wallet keys to the browser or README.

## API reference

All application endpoints use `/api`. Workspace routes require `X-Session-ID: <UUID>`; the browser generates an unpredictable UUID in local storage under `parity-session`. SSE uses `session_id` as a query parameter. Treat this identifier as private workspace access, not full user authentication.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Service, public feed and Binance status |
| GET | `/api/state` | Selected market, settings, decision, manual eligibility, Binance reference, wallet |
| GET | `/api/stream?session_id=…` | Event-driven SSE; one-second fallback; 30-second connection rotation |
| PUT | `/api/settings` | Validate and persist the complete settings model |
| GET | `/api/reference` | Binance public references for all supported assets |
| POST | `/api/paper/manual/quote` | One-side quote: `side`, `amount`, `unit` (`dollars` or `shares`) |
| POST | `/api/paper/manual/execute` | Buy quoted side: `quote_id`, unique `request_id`, `accept_directional_risk: true` |
| POST | `/api/paper/quote` | Recompute and issue a strict executable quote from the current `decision_id` |
| POST | `/api/paper/execute` | Execute a strict quote: `quote_id`, `request_id`, optional stress `scenario` |
| GET | `/api/trades` | Workspace's latest 300 records, newest first |
| POST | `/api/trades/{id}/recover` | Reevaluate an automatic RECOVERY_REQUIRED position |
| POST | `/api/simulate` | Manual two-leg payoff, `up_shares` and `down_shares` |
| POST | `/api/live/execute` | Always HTTP 423; real trading is locked |
| GET | `/api/readme` | Download this README |

Typical errors: `400` invalid workspace identifier; `409` expired quote/stale book/rollover/insufficient depth; `422` invalid amount, precision, minimum size, constraints or missing risk acceptance; `423` real trading locked. A paper order that fails after submission is recorded as ABORTED, with its reason in the ledger.

## Tests

```bash
# From the repository root, with backend dependencies installed
pytest -q -n 0 backend/tests tests
```

The suites cover book walking/fees, exact share increments, objective/brute-force comparisons, monetary limits, stale/sync guards, idempotency, paper fills/recovery, API contracts and SSE reliability. Public API tests read `REACT_APP_BACKEND_URL` from the frontend environment, so configure it to the running backend/API origin. Frontend verification uses browser navigation, actual paper orders and responsive screenshots. New feature checks cover Binance data and independent buys.

Tests may use controlled book/storage fixtures; the app itself never replaces unavailable live feeds with synthetic prices or trades. Live opportunities are not guaranteed, so deterministic math/recovery scenarios use isolated fixtures rather than forcing a false recommendation.

## Safety and current limitations

- **Paper-only execution.** Real wallet authentication, signing, approvals, balance/allowance checks and real orders are not implemented. Do not use this as a live-money trading service.
- The optimizer is not a BTC/ETH/SOL prediction model. Binance charts provide context, not buy signals or settlement authority.
- Manual orders are directional and can lose their entire cost. Automatic worst-case calculations assume the planned legs fill; execution failures can create additional exposure.
- The public CLOB does not publish a guaranteed gap-free sequence protocol. Parity validates and rebuilds conservatively; it does not claim perfect recovery from every exchange anomaly.
- The strict engine uses exact Decimal execution math, piecewise depth bounds and ascending quantity checks. It does not assume global profitability is monotonic. If the smallest qualifying quantity cannot be proven within the calculation limit, no order is authorized. Fee-rounding bounds only prune the search; final eligibility always requires the exact $0.12 threshold.
- Paper execution approximates latency, liquidity loss and failures. It cannot reproduce every real matching-engine, queue-priority, settlement, network or oracle behavior.
- Quotes are durable MongoDB records bound to workspace, strategy/policy, market/condition/token IDs, Q, fee/tick/quantity configuration, envelope, settings and fixed expiry. Atomic single-use claims prevent different request IDs reusing a quote. Consumed or interrupted quotes are never reopened. Quote issuance happens on purchase intent, not every SSE tick. Cash handling still uses single-process locks and separate ledger writes: transactional cash reservation and full crash-consistent balances remain prerequisites before real-money or multi-worker operation.
- Official price-to-beat extraction and direct oracle subscriptions are not available. The UI labels that reference unavailable rather than substituting Binance.
- No guarantee of profit or constant trade recommendations. **Keeping the entire pool in cash is often the correct result.**