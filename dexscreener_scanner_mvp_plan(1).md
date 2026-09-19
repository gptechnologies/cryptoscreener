# Dexscreener Live Scanner — Base MVP Implementation Plan

## 1. MVP Goal

Build the smallest working scanner that proves this workflow:

1. Watch the Dexscreener **New Pairs** page for Solana.
2. Detect new pairs as they appear.
3. Add every newly discovered pair to a **Candidate Pairs** table.
4. Evaluate each candidate against the first-candle volume rules:
   - First **1-minute candle** volume >= **$5,000**
   - OR first **5-minute candle** volume >= **$10,000**
   - OR first **1-hour candle** volume >= **$100,000**
5. If a pair passes any rule, move/copy it into a **Qualified Scanner** table.
6. If it fails all three rules, remove it from active tracking.
7. For qualified tokens, continuously refresh the normal market data from Dexscreener's public API.

The MVP does **not** need to be polished. It should prove that discovery, qualification, and live tracking work reliably.

---

## 2. What the MVP UI Should Look Like

Use a single page with only two tables.

### Table A — Candidate Pairs

This table proves that the discovery process is working.

Suggested columns:

| Column | Purpose |
|---|---|
| Seen At | Time our watcher first detected the pair |
| Token | Symbol / name |
| Age | Current pair age |
| Pair Address | Pair identifier |
| Token Address | Base token identifier |
| DEX | PumpSwap, Raydium, Meteora, etc. |
| 1m #1 Vol | First 1-minute candle volume |
| 5m #1 Vol | First 5-minute candle volume |
| 1h #1 Vol | First 1-hour candle volume |
| Status | Watching 1m / Watching 5m / Watching 1h / Qualified / Failed |

Rows should appear here as soon as Playwright sees them on Dexscreener.

### Table B — Qualified Scanner

Only tokens that pass at least one filter appear here.

Suggested columns:

| Column | Purpose |
|---|---|
| Qualified At | Time the filter was triggered |
| Token | Symbol / name |
| Trigger | 1M / 5M / 1H |
| Trigger Volume | Candle volume that qualified it |
| Age | Current pair age |
| Price | Current price |
| MCAP | Current market cap |
| Volume | Current Dexscreener volume |
| Liquidity | Current liquidity |
| 5M % | Current 5-minute change |
| 1H % | Current 1-hour change |
| TXNS | Current transaction count |
| Traders | Current trader count if available |
| Pair | Link to Dexscreener |
| Contract | Copyable token address |

No charts are needed in the MVP.

---

## 3. Recommended MVP Stack

### Backend
- Python 3.12+
- FastAPI
- Playwright
- asyncio
- httpx
- SQLAlchemy or lightweight direct SQLite access

### Database
Start with SQLite.

Reason:
- Single-user MVP
- Very small dataset
- Easy to inspect
- No infrastructure setup

Use PostgreSQL later only if needed.

### Frontend
- React
- Vite
- Simple CSS
- Fetch or Server-Sent Events for updates

Avoid heavy UI libraries unless already present.

### Real-time transport
Use **Server-Sent Events (SSE)** for the MVP.

Why:
- Backend only needs to push updates to the browser
- Simpler than WebSockets
- Perfect for new rows / updated rows

Polling the backend every 2–3 seconds is also acceptable for the first test if that is faster to build.

---

## 4. High-Level Architecture

```text
                   DEXSCREENER
                        |
                        |
              Playwright Headless Browser
                        |
               New Pairs / Age Ascending
                        |
                        v
                Pair Discovery Service
                        |
                        v
                  Candidate Queue
                        |
                        v
               Candle Qualification
              /          |           \
           1m           5m            1h
         >= $5K       >= $10K      >= $100K
              \          |           /
                        v
                 Qualified Pairs
                        |
                        v
              Dexscreener Public API
                        |
                        v
               Live Market Data Refresh
                        |
                        v
                    FastAPI
                        |
                        v
                 React Two-Table UI
```

---

## 5. Discovery Strategy

### Use Playwright for discovery only

Do **not** use Playwright to monitor every pair after discovery.

The browser's job is only:

> Tell us which new pairs Dexscreener is currently surfacing.

Open the exact Dexscreener page used manually:

- Chain: Solana
- Section: New Pairs
- Rank/sort: Age ascending / newest first
- Same filters used manually

The watcher should read the current table every 1–2 seconds.

### Discovery loop

Pseudo-code:

```python
seen_pairs = set()

while True:
    rows = await read_visible_dexscreener_rows()

    for row in rows:
        pair_address = row.pair_address

        if pair_address not in seen_pairs:
            seen_pairs.add(pair_address)

            await create_candidate(
                pair_address=pair_address,
                token_address=row.token_address,
                symbol=row.symbol,
                name=row.name,
                dex=row.dex,
                first_seen_at=now()
            )

    await asyncio.sleep(1.5)
```

### Important

Persist `seen_pairs` in the database, not only memory.

If the backend restarts, we should not rediscover and reprocess every currently visible pair.

---

## 6. Playwright Extraction

For each visible row, extract at minimum:

```text
pair_address
token_address
symbol
name
dex
dexscreener_pair_url
displayed_age
```

Optional immediately:

```text
price
mcap
volume
liquidity
txns
traders
```

However, these optional fields do not need to be trusted long-term because the API will refresh qualified pairs later.

### Prefer extracting addresses from links

Do not parse addresses from visible text if the row contains a Dexscreener pair link.

Use the `href` because it is less fragile.

Example concept:

```python
href = await row.locator("a").get_attribute("href")
pair_address = parse_pair_from_href(href)
```

### Selector strategy

Avoid fragile selectors such as:

```text
:nth-child(4)
div > div > div > span
```

Prefer:

- semantic table row containers
- anchor href patterns
- `data-*` attributes if available
- text headers only as a fallback

Put every Dexscreener selector in one file:

```text
services/dexscreener_selectors.py
```

so site changes are easy to fix.

---

## 7. Candidate Lifecycle

Each candidate should have a simple state.

Recommended states:

```text
NEW
WATCHING_1M
WATCHING_5M
WATCHING_1H
QUALIFIED
FAILED
ERROR
```

Typical path:

```text
NEW
 ↓
WATCHING_1M
 ↓ fail
WATCHING_5M
 ↓ fail
WATCHING_1H
 ↓ fail
FAILED
```

Any stage can instead become:

```text
QUALIFIED
```

Once qualified, no further filter is required for inclusion in the scanner.

We may continue recording later candle values for research, but that is not required for the MVP.

---

## 8. Candle Qualification Rules

These are **Dexscreener candle volumes**, not rolling windows calculated from pair creation time.

The user wants the same number visible in Dexscreener's TradingView-style chart volume indicator.

### Rules

```text
first Dexscreener 1m candle volume >= 5,000
OR
first Dexscreener 5m candle volume >= 10,000
OR
first Dexscreener 1h candle volume >= 100,000
```

### Important

The first 5-minute candle is Dexscreener's first 5-minute chart bucket.

The first 1-hour candle is Dexscreener's first 1-hour chart bucket.

Do not redefine these as:

```text
first 5 minutes since pair creation
first 60 minutes since pair creation
```

The goal is exact parity with the candle shown by Dexscreener.

---

## 9. Dexscreener Candle Endpoint Already Identified

We identified the chart-bars route used by Dexscreener.

Example pattern:

```text
https://io.dexscreener.com/dex/chart/amm/v3/{dex}/bars/{chain}/{pair_address}
```

Example Solana / PumpSwap request:

```text
https://io.dexscreener.com/dex/chart/amm/v3/pumpfundex/bars/solana/{PAIR}
?mc=1
&res=5
&cb=329
&q=So11111111111111111111111111111111111111112
&uo=0
```

Confirmed resolution behavior:

```text
res=1   -> 1-minute chart
res=5   -> 5-minute chart
res=60  -> 1-hour chart
```

We also observed smaller incremental requests resembling:

```text
?mc=1
&abn=...
&ats=...
&res=1
&cb=2
&q=...
&uo=0
```

These appear related to live/incremental bar updates.

---

## 10. Required Technical Spike Before Qualification Logic

The `/bars/` response is serialized/binary rather than easy JSON.

Before building the full candidate state machine, implement one small spike:

### Objective

Given one known Solana pair:

```python
bars = await fetch_dexscreener_bars(
    pair_address=PAIR,
    dex="pumpfundex",
    resolution=1
)
```

return:

```python
[
    {
        "timestamp": ...,
        "open": ...,
        "high": ...,
        "low": ...,
        "close": ...,
        "volume": ...
    }
]
```

### Acceptance test

Take one known Dexscreener pair.

For each timeframe:

```text
1m
5m
1h
```

compare:

```text
decoded first candle volume
```

against:

```text
the Volume number shown by Dexscreener when manually hovering the first candle
```

They must match.

Do not proceed to scanner qualification until this works.

### If decoding the endpoint takes too long

Fallback for MVP only:

1. Continue using Playwright.
2. Open candidate pair pages in a small controlled browser pool.
3. Read the data from Dexscreener's own frontend/runtime if accessible.
4. Use that only until the bars decoder is complete.

Do **not** build computer-vision/pixel scraping of the chart.

---

## 11. Candidate Evaluation Schedule

Do not hammer all three timeframes constantly.

Use lifecycle-aware checks.

### New pair

Immediately request:

```text
res=1
```

Determine first 1m candle volume.

If it is still forming, refresh it periodically.

### If 1m qualifies

```text
QUALIFIED
```

Stop qualification checks.

### If first 1m candle closes below $5K

Move to:

```text
WATCHING_5M
```

Check:

```text
res=5
```

### If 5m qualifies

```text
QUALIFIED
```

### If first 5m candle closes below $10K

Move to:

```text
WATCHING_1H
```

Check:

```text
res=60
```

### If 1h qualifies

```text
QUALIFIED
```

### If first 1h candle closes below $100K

```text
FAILED
```

Remove it from active candidate tracking.

---

## 12. Polling Frequency for Candidates

Start conservative.

Suggested MVP intervals:

```text
WATCHING_1M -> every 3 seconds
WATCHING_5M -> every 5 seconds
WATCHING_1H -> every 15 seconds
```

This can be optimized later.

The filter should trigger as soon as the currently forming first candle crosses the threshold.

Do not wait for the candle to close if it has already qualified.

Example:

```text
first 5m candle
elapsed: 2m 14s
volume: $10,440
```

Qualify immediately.

---

## 13. Qualified Token Data Refresh

Once a pair qualifies, stop relying on Playwright for normal market data.

Use Dexscreener's documented/public pair or token API.

Refresh fields such as:

```text
price
mcap
fdv
volume
liquidity
priceChange
txns
pairCreatedAt
```

If trader count is not available from the public endpoint, omit it from MVP or keep it from another Dexscreener source later.

### Refresh cadence

For the MVP:

```text
qualified tokens -> refresh every 5 seconds
```

If the API supports batching, batch qualified tokens instead of making one request per token.

The scanner only needs fast enough updates to visually feel live.

---

## 14. Database Model

### `pairs`

```sql
id
chain
pair_address
token_address
dex_id
symbol
name
dexscreener_url
first_seen_at
pair_created_at
status
qualified_at
qualification_trigger
qualification_volume
created_at
updated_at
```

Unique constraint:

```text
(chain, pair_address)
```

### `candidate_candles`

```sql
id
pair_id
timeframe
first_candle_timestamp
volume
is_closed
last_checked_at
```

Possible timeframe values:

```text
1m
5m
1h
```

### `qualified_market_snapshot`

For MVP, one current row per pair is enough.

```sql
pair_id
price_usd
market_cap
fdv
volume
liquidity
price_change_5m
price_change_1h
txns
traders
updated_at
```

Historical snapshots are not required for V1.

---

## 15. Backend Services

Recommended structure:

```text
backend/
  app/
    main.py

    api/
      candidates.py
      qualified.py
      events.py

    services/
      playwright_discovery.py
      dexscreener_bars.py
      dexscreener_api.py
      qualification_engine.py
      market_refresher.py

    models/
      pair.py
      candle.py
      market_snapshot.py

    db/
      database.py

    config.py
```

---

## 16. Frontend Structure

Recommended:

```text
frontend/
  src/
    App.jsx

    components/
      CandidateTable.jsx
      QualifiedTable.jsx
      StatusBadge.jsx

    api/
      client.js

    styles/
      app.css
```

Single route only:

```text
/
```

No authentication.

No navigation.

No settings page.

No modal system unless needed.

---

## 17. Minimal Visual Design

Use a clean dark trading-terminal style.

### Page

```text
DEXSCREENER SCANNER
SOLANA
● LIVE

Candidate Pairs
--------------------------------------------------

Qualified Scanner
--------------------------------------------------
```

Candidate table should visually communicate:

```text
WATCHING 1M
WATCHING 5M
WATCHING 1H
QUALIFIED
FAILED
```

Qualified table should make the trigger obvious:

```text
1M  $6.2K
5M  $14.8K
1H  $126K
```

Keep everything dense and readable.

No cards unless necessary.

No charts.

No animations other than perhaps a small LIVE indicator.

---

## 18. Backend API

### Candidates

```http
GET /api/candidates
```

Return active candidates.

### Qualified

```http
GET /api/qualified
```

Return all qualified rows.

### Event stream

```http
GET /api/events
```

SSE events:

```text
candidate_added
candidate_updated
candidate_removed
qualified_added
qualified_updated
```

Example:

```json
{
  "type": "qualified_added",
  "data": {
    "pair_address": "...",
    "symbol": "ABC",
    "trigger": "1m",
    "trigger_volume": 6241.33
  }
}
```

---

## 19. Discovery Resilience

Playwright is the fragile part, so isolate it.

### Browser configuration

Run Chromium headless.

```python
browser = await chromium.launch(
    headless=True
)
```

Create one persistent page for Dexscreener discovery.

Do not create a browser per pair.

### Automatic recovery

If:

```text
page crashes
browser disconnects
Dexscreener reloads
selector fails
```

then:

1. Log the error.
2. Restart the page/browser.
3. Reload the configured New Pairs URL.
4. Resume using database-backed seen pairs.

### Health fields

Expose:

```text
browser_connected
last_successful_scan
last_pair_seen
candidate_count
qualified_count
```

---

## 20. Logging

Use structured logs.

Important events:

```text
DISCOVERY_PAIR_FOUND
CANDIDATE_CREATED
CANDLE_1M_UPDATED
CANDLE_1M_FAILED
CANDLE_5M_UPDATED
CANDLE_5M_FAILED
CANDLE_1H_UPDATED
PAIR_QUALIFIED
PAIR_FAILED
MARKET_DATA_UPDATED
PLAYWRIGHT_RESTARTED
DEXSCREENER_REQUEST_FAILED
```

Example:

```text
PAIR_QUALIFIED
pair=ABC123
symbol=ABC
trigger=5m
volume=14287.44
```

---

## 21. Configuration

Use `.env`.

Example:

```env
CHAIN=solana

DEXSCREENER_NEW_PAIRS_URL=...

DISCOVERY_INTERVAL_SECONDS=1.5

ONE_MIN_VOLUME_THRESHOLD=5000
FIVE_MIN_VOLUME_THRESHOLD=10000
ONE_HOUR_VOLUME_THRESHOLD=100000

ONE_MIN_POLL_SECONDS=3
FIVE_MIN_POLL_SECONDS=5
ONE_HOUR_POLL_SECONDS=15

QUALIFIED_REFRESH_SECONDS=5

DATABASE_URL=sqlite:///./scanner.db
```

Do not hard-code thresholds in the qualification engine.

---

## 22. Important Deduplication Rules

A token may have multiple pairs.

For the MVP, qualify and track by:

```text
pair_address
```

not only token address.

Reason:

Dexscreener is showing pairs, and the candle data is pair-specific.

Store the base token address separately.

If two different pairs exist for the same token, treat them as separate discoveries initially.

We can later define a preferred-pair rule if needed.

---

## 23. Failure Handling

### Candidate candle fetch fails

Keep candidate active and retry.

After repeated failures:

```text
status = ERROR
```

Do not silently discard it.

### Dexscreener API fails

Keep the previous market snapshot and show:

```text
last updated: X seconds ago
```

### Playwright stops discovering pairs

UI should clearly show stale discovery state.

Example:

```text
● LIVE
```

becomes:

```text
● DISCOVERY STALE — 42s
```

---

## 24. MVP Acceptance Tests

The MVP is complete when all of these work.

### Test 1 — Discovery

Open the same Dexscreener New Pairs page manually.

When a new pair appears there:

```text
within ~2–4 seconds
```

it appears in our Candidate Pairs table.

### Test 2 — No duplicates

The same pair never appears twice.

### Test 3 — 1m qualification

A pair whose first 1m candle exceeds $5K:

```text
moves to Qualified Scanner
```

with:

```text
trigger = 1M
```

### Test 4 — 5m qualification

A pair that fails 1m but whose first 5m candle exceeds $10K:

```text
moves to Qualified Scanner
```

with:

```text
trigger = 5M
```

### Test 5 — 1h qualification

A pair that fails 1m and 5m but whose first 1h candle exceeds $100K:

```text
moves to Qualified Scanner
```

with:

```text
trigger = 1H
```

### Test 6 — Failure

A pair that fails all three:

```text
is removed from active tracking
```

after its first 1h candle closes.

### Test 7 — Candle parity

For manually sampled pairs:

```text
our first candle volume
=
Dexscreener first candle Volume indicator
```

for 1m / 5m / 1h.

### Test 8 — Qualified live updates

After qualification:

```text
price
market cap
volume
liquidity
price changes
transactions
```

continue refreshing without Playwright visiting the pair page.

---

## 25. Development Order

### Phase 0 — Candle decoder spike

Build:

```text
fetch pair bars
decode response
return OHLCV
```

Validate against Dexscreener manually.

Do this first because qualification depends on it.

### Phase 1 — Playwright discovery

Build:

```text
open New Pairs page
read rows
extract pair addresses
persist new pairs
```

Output to console first.

### Phase 2 — Candidate database

Persist discovered pairs and statuses.

### Phase 3 — Qualification engine

Implement:

```text
1m -> 5m -> 1h
```

state transitions.

### Phase 4 — Public API refresher

Refresh market data for qualified pairs.

### Phase 5 — FastAPI endpoints

Expose:

```text
candidates
qualified
health
```

### Phase 6 — React page

Create the two tables.

### Phase 7 — Live updates

Add SSE or simple 2-second frontend polling.

### Phase 8 — Run for several hours

Compare scanner behavior against manual Dexscreener observation.

---

## 26. What Is Explicitly Out of Scope

Do not add any of these to the base MVP:

```text
Robinhood Chain
multiple users
authentication
alerts
Telegram
Discord
email
desktop notifications
charts
trade execution
wallet integration
risk management
historical backtesting
advanced filters
saved views
cloud scaling
Redis
Kafka
microservices
machine learning
on-chain DEX discovery
factory subscriptions
Solana program subscriptions
mobile app
```

First prove:

```text
discover -> evaluate -> qualify -> live update
```

---

## 27. Suggested Local Run Commands

Backend:

```bash
cd backend

python -m venv .venv
source .venv/bin/activate

pip install fastapi uvicorn playwright httpx sqlalchemy aiosqlite
playwright install chromium

uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend

npm install
npm run dev
```

---

## 28. Suggested MVP Screen

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ DEXSCREENER SCANNER                                      SOLANA   ● LIVE    │
├──────────────────────────────────────────────────────────────────────────────┤
│ CANDIDATE PAIRS                                                            │
│                                                                            │
│ Seen     Token   Age   1M #1   5M #1   1H #1   Status                     │
│ 11:32:01 ABC     21s   $2.1K   —       —       WATCHING 1M                │
│ 11:31:42 XYZ     40s   $4.7K   —       —       WATCHING 1M                │
│ 11:29:12 CAT      3m   $3.2K   $8.4K   —       WATCHING 5M                │
│                                                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ QUALIFIED SCANNER                                                          │
│                                                                            │
│ Time    Token Trigger   Price   MCAP    Volume   Liq    5M    1H   Txns   │
│ 11:31   DOG   1M $6.2K  .0042   $92K    $12K     $18K   31%   31%   81    │
│ 11:27   FOO   5M $14K   .0018   $410K   $31K     $42K   18%   95%  191    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 29. Definition of Done

The base MVP is done when it can run unattended locally and:

1. Detect new Solana pairs appearing on the chosen Dexscreener New Pairs page.
2. Add them to the Candidate Pairs table.
3. Evaluate the exact Dexscreener first-candle volume for 1m, 5m, and 1h.
4. Immediately qualify a pair when any threshold is crossed.
5. Remove candidates that fail all three checks.
6. Display qualified pairs in a second table.
7. Continuously update qualified-pair market data through Dexscreener's API.
8. Recover from browser refresh/restart without duplicating pairs.
9. Match manually inspected Dexscreener candle-volume values.
10. Stay stable for a multi-hour test session.

---

## 30. Recommended First Implementation Ticket

Start with this single ticket:

> **Build the Dexscreener Solana discovery + candle decoder spike.**
>
> 1. Launch Dexscreener New Pairs using Playwright.
> 2. Log every unique pair address that appears.
> 3. For one discovered pair, request `/bars/` with `res=1`, `res=5`, and `res=60`.
> 4. Decode each response into timestamp/OHLC/volume records.
> 5. Print the first candle volume for each timeframe.
> 6. Manually compare the numbers with Dexscreener.
>
> Do not build the UI until this end-to-end technical path works.

Once that ticket succeeds, the rest of the MVP is straightforward application plumbing.
