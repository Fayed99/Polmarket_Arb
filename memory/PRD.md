# Parity — Polymarket Short-Term Opportunity Optimizer

## Current approved strategy — 2026-09-18 (supersedes generalized automatic objectives below)
- User explicitly approved strict arbitrage as the sole automatic recommendation path: UP = DOWN = Q, combined fee-inclusive cost >= $1.00, guaranteed profit >= $0.12 ($0.10 immutable floor + $0.02 immutable buffer), and smallest qualifying Q. No maximizing deployment or profit dollars.
- Implemented `strict_policy.py`, `execution_math.py`, `strict_arbitrage.py`, `quote_store.py`, and `settings_migration.py`. Shared Decimal math exposes guaranteed payout=min(UP,DOWN), guaranteed profit, and unmatched exposure. Exact ascending quantity checks use conservative piecewise fee/depth bounds; calculation limits fail closed.
- Hard economic envelope binds combined/per-leg debit and tick-aligned price limits. Paper entry revalidates market/tokens/config, Q minimality, book safety, depth, fees and policy. Strict recovery cannot reduce the floor/buffer. Unexpected post-fill nonqualification is RECONCILIATION_EXCEPTION, not labeled arbitrage and not topped up.
- Automatic API uses strict decisions, then POST `/api/paper/quote` on purchase intent, then execution. Quotes persist in MongoDB with immutable market/config/settings/Q/envelope binding, timestamps/hash evidence, expiry and atomic single-use state. Manual directional quotes use the same durable store with separate kind; manual trading stays paper-only and explicitly non-arbitrage.
- Generalized optimizer retained only for legacy implementation/tests. Active settings reject generalized objectives and threshold overrides. Existing workspace common settings are preserved; old strategy settings archived. Existing trades are not reclassified.
- UI changes limited to strict controls and Q/guaranteed economics/envelope displays. No changes to Binance ingestion, feed mechanics, or `/api/live/execute`; real trading remains locked. No AI integration.
- Validation: `/app/test_reports/iteration_4.json`, existing + necessary strict tests: 50 passed, 1 skipped, 0 failed. Focused UI check passed. Skipped public strict replay check saw no actual READY opportunity within 60 seconds; deterministic strict execution and durable-claim tests passed. Tests migrated only where the old settings/in-memory-quote contract was intentionally replaced.
- Limitations retained: paper latency/failure/liquidity settings are synthetic, not exchange measurements; quote durability is not a fully transactional financial ledger. Cash locks and separate ledger writes remain single-process; real-money and multi-worker readiness are not claimed.
- P0: no reported defects in tested strict scope. P1: observe a real qualifying strict paper pair naturally; full transactional cash/execution durability before any separate real-money phase. Historical product requirements below describe earlier phases, not current automatic strategy.

## Original problem statement / single source of truth
Build a real-time multi-asset Polymarket short-duration cryptocurrency Up/Down opportunity optimizer. Initially BTC, ETH, and SOL 5-minute markets, with a generic registry for future assets/durations. The central user question is: “Given the money I have available, is there a worthwhile Up/Down trade right now? If so, how much money should I actually deploy, how should I split it between Up and Down, and what happens if either side wins?”

**The bot is not supposed to spend the user's budget.** Budget Pool is a ceiling, not trade size. Automatically search deployment AND allocation, including executable book depth, fees, liquidity, min size, tick and quantity precision. A $100 pool may recommend $0, $3, $8, $20, or $100. No trade is valid. Total debit (purchase cost + fees) must never exceed available pool or paper account cash.

Source requirements supplied in 31 sections:
1. Generic current-market discovery: IDs, condition, outcome token mapping, start/end, status, fees, min size, price tick, quantity precision; automatic rollover.
2. CLOB WebSocket full snapshots + absolute price-change deltas into complete local books. Immediate event-triggered recalculation plus at least one-second fallback. REST only for snapshots/recovery.
3. Separate exchange event and local arrival timestamps. Default cross-book synchronization threshold 100 ms. Fail closed on stale, unsynchronized, crossed, incomplete or invalid books; discard/rebuild.
4. Walk both ask ladders, compute actual shares/cost/fees/VWAP/highest ask/levels/slippage; fees at each execution price.
5. Auto objectives: guaranteed both-outcome profit after fees, capital protected/controlled loss, maximum minimum payoff, maximum upside with loss ceiling, custom objective/deployment/allocation constraints. Manual simulator secondary.
6. Decision-first UI: budget/deployed/unspent, exact UP/DOWN purchase cost, fee, debit, shares and average price; both payouts/profits, worst/best, ROI and BUY RECOMMENDED TRADE.
7. Execution revalidation: latest books, freshness, sync, balance, minimums, precision, liquidity, slippage, objective; no stale recommendation submitted.
8. One-account two-leg architecture. Concurrent does not mean atomic. FOK per leg, reconciliation using actual fills, recovery retry or unwind constrained by loss cap, explicit open exposure if recovery is unsafe.
9. Realistic paper latency, changing/disappearing liquidity, failed legs, stale/desynced feeds and recovery. Paper is safe default; real orders explicitly enabled only with backend-held credentials, never secrets in frontend.
10. Real positions and settlement tracking; exact distinctions between budget, cost, debit, shares, payout and profit.

## Clarification / accepted initial scope
User selected all offered choices ambiguously. Agent explicitly proceeded with recommended defaults without another blocking question: **real public market data + realistic paper execution**, real orders locked; $100 budget, $1 max loss, Maximum Upside. The user then requested continuation. No wallet credentials or live-trading authorization were provided. No LLM integration needed.

## Personas
- Budget-conscious short-duration market trader who wants a ready-to-understand allocation rather than manual ratios.
- Risk-focused paper trader verifying both outcome payoffs and execution failure/recovery before authorizing real trading.

## Architecture decisions
- React 19 / React Router, existing shadcn Button/Input and Sonner, DM Sans/Barlow Condensed/JetBrains Mono. Dark neutral trading terminal with cyan actions, green/red outcome semantics. Responsive sidebar and standalone feature routes.
- FastAPI service on supervisor-managed port 8001; public API exclusively `/api`. Existing protected frontend backend URL and Mongo URL preserved.
- MongoDB sessions + trade ledger, UUID identifiers, `_id` excluded from responses; Pydantic response models for stored records.
- Unpredictable browser UUID scopes anonymous paper workspaces (not authenticated identity). Initial paper cash $1,000 independent of $100 allocation pool. No sensitive wallet data.
- `feed.py`: configurable series registry in backend env; public Gamma epoch-based discovery, authoritative CLOB market info, REST bootstrap/rebuild and per-asset WebSocket stream. Snapshot and absolute deltas validated, failure state clears readiness and reconnects with backoff. Exchange errors never replaced with synthetic data.
- `books.py`: Decimal tick validation, separate event/arrival times, full sorted books, size precision from installed `py_clob_client_v2` ROUNDING_CONFIG. e=1 fee curves supported; unknown curves fail closed.
- `optimizer.py`: SciPy HiGHS mixed-integer search of quantity increments and piecewise-convex debit envelopes. Searches both asymmetric upside directions or max-min payoff, then Decimal book-walk verification. Safety rounding reserve, zero-deployment baseline, explicit maximum deployment/cash constraints. Custom allocation limits refer to UP percentage of combined **shares**.
- Authoritative current V2 fee formula verified directly against official fees/migration docs and actual CLOB metadata: **shares × fee rate × p × (1−p)**, collateral-denominated fees rounded 5 decimals. Do not use old notional-based or V1 buy-share deductions. Actual current market rate observed 0.07; runtime rate always used.
- SSE downstream with Event broadcast, bounded 30-second connections and 300 ms browser reconnect. Event-driven calculation offloaded to worker thread; 1-second fallback wait and client stale watchdog. Bounded shutdown avoids hanging while draining feeds.
- `paper.py`: quote stored on backend (10-second lifetime), scoped lock/idempotency, preflight against current fixed quantities and approved debit, independent FOK legs and post-latency live books. Probabilistic failures and retained-liquidity haircut; stress scenario controls. Missing-leg retry or bid-side unwind (sell fees included); unsafe exits leave RECOVERY_REQUIRED. Actual fills and wallet debit reconciled.
- Settlement worker credits paper outcomes only after Gamma confirms closed + resolved + exact binary prices; no inference from spot prices or market expiration alone.

## Implemented — 2026-09-18
- Live BTC/ETH/SOL discovery, automatic five-minute rollover, full/delta CLOB feeds and health diagnostics.
- Five automatic objectives, $100/$1 default parameters, quick pool presets, maximum deployment, minimum profit and custom share allocation constraints; persistent settings, pause/resume.
- Clear decision screen including positive opportunity and zero-deployment no-trade states, exact cost/fee/share/VWAP details, conditional outcome P&L and risk classification.
- Real-data paper execution, configurable latency/failures/liquidity, one-sided recovery, actual-fill records and balance ledger.
- Positions and trade activity, per-leg reconciliation, event timeline, manual recovery reevaluation, asset filters and CSV export.
- Paper lab: manual quantities using current books, and named execution failure scenarios using actual valid recommendations.
- Market-data route: live ladders and depth bars, event/arrival ages, IDs, token mapping, ticks/min sizes, fee formula and guards.
- Settings route for sync, staleness, slippage, paper simulation and recovery limits.
- `/api/live/execute` always returns HTTP 423. No real signing, approvals, allowance or wallet-balance integrations exist in this phase.

## Verification / fixes
- Production frontend build succeeded.
- Iteration 1 found a brute-force comparison mismatch. Fixed centralized slippage validation (oracle had admitted off-limit levels) and a genuine floating-point liquidity bound bug (`floor(5.1/.01)` dropped an increment). Bound calculations now use exact Decimal integer rounding.
- Replaced an asyncio.Condition streaming deadlock with Event broadcast. Bounded streaming sessions/shutdown resolved update-time hangs.
- `/app/test_reports/iteration_2.json`: **27/27 backend checks passed**, no remaining reported frontend/backend defects. Public API, SSE rotation and health, objective/brute-force cases, precision, stale/sync checks, quote expiry/idempotency/session scoping, paper fills/recovery/balance verified.
- Browser verification covered routes, settings stability during streaming, persistence, pause/resume, asset selection, desktop/mobile no overflow. Real live-data READY recommendation executed in a fresh paper session; ledger confirmed. One-sided stress produced UNWOUND; stale/unsynced stress produced ABORTED.
- No naturally resolved position completed during the short verification window. Settlement guard paths are implemented, but end-to-end natural settlement credit timing remains to be observed. Do not claim it was empirically verified.
- Test suites: `/app/backend/tests/`, `/app/tests/test_reliability_regression.py`. Reports/screenshots under `/app/test_reports/`. No production API mocks; order execution is deliberately paper simulation.

## Prioritized backlog / scope boundaries
### P0
- None open in verified paper MVP. Real trading remains strictly locked, not silently enabled.
### P1 — next phase
- Backend-only authenticated wallet/account integration, regional eligibility, current V2 signing, real balance/allowances and explicit authorization; small controlled live execution, actual exchange fill reconciliation and real recovery. Never solicit/store private keys in frontend.
- Observe natural resolved-market settlement end-to-end and validate delayed/disputed resolution handling.
- Harden financial ledger with atomic cash reservation and durable execution jobs/transactions before multi-worker or real-money use. Current paper account locks are single-process.
- Add configurable partial-fill/FAK policies; this MVP deliberately uses FOK for both paper legs.
### P2
- Cross-asset opportunity ranking/alerts, more registered assets/durations and corresponding UI controls.
- Recorded-market replay and longer-window paper performance analytics.
- Event-sequence/hash reconciliation enhancements and distributed per-strategy computation for more simultaneous sessions.

## Next tasks
1. Add opportunity notifications so worthwhile trades do not require continuous screen watching.
2. Observe naturally settled paper positions and expand settlement-specific regression fixtures.
3. Gather explicit authenticated live-execution scope before implementing any real-money capability.

## Running app
Public app: https://arb-finder-5m.preview.emergentagent.com
Use existing supervisor services; do not start separate uvicorn processes. Preserve protected environment values. All product market/API endpoints use configured environment URLs.
