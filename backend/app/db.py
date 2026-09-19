from __future__ import annotations

import sqlite3
import time
import json
from pathlib import Path


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, path: Path | str = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.ignored_pairs: set[tuple[str, str]] = set()
        self.conn = sqlite3.connect(str(path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pairs (
                id INTEGER PRIMARY KEY,
                chain TEXT NOT NULL,
                discovery_key TEXT NOT NULL,
                pair_address TEXT NOT NULL,
                token_address TEXT,
                quote_address TEXT,
                dex_id TEXT,
                symbol TEXT NOT NULL DEFAULT '…',
                name TEXT,
                dexscreener_url TEXT NOT NULL,
                first_seen_at INTEGER NOT NULL,
                pair_created_at INTEGER,
                status TEXT NOT NULL DEFAULT 'NEW',
                stage TEXT NOT NULL DEFAULT '1m',
                next_check_at INTEGER NOT NULL,
                error_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                qualified_at INTEGER,
                qualification_trigger TEXT,
                qualification_volume REAL,
                next_market_refresh_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(chain, discovery_key),
                UNIQUE(chain, pair_address)
            );
            CREATE INDEX IF NOT EXISTS idx_pair_candidate_due ON pairs(status, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_pair_market_due ON pairs(status, next_market_refresh_at);
            CREATE TABLE IF NOT EXISTS candidate_candles (
                pair_id INTEGER NOT NULL REFERENCES pairs(id),
                timeframe TEXT NOT NULL,
                first_candle_timestamp INTEGER NOT NULL,
                volume_usd REAL NOT NULL,
                is_closed INTEGER NOT NULL,
                last_checked_at INTEGER NOT NULL,
                PRIMARY KEY(pair_id, timeframe)
            );
            CREATE TABLE IF NOT EXISTS qualified_market_snapshot (
                pair_id INTEGER PRIMARY KEY REFERENCES pairs(id),
                price_usd REAL,
                market_cap REAL,
                fdv REAL,
                volume_m5 REAL,
                volume_h1 REAL,
                volume_h24 REAL,
                liquidity_usd REAL,
                price_change_5m REAL,
                price_change_1h REAL,
                txns_m5 INTEGER,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qualified_rvol (
                pair_id INTEGER NOT NULL REFERENCES pairs(id),
                timeframe TEXT NOT NULL CHECK(timeframe IN ('1m', '5m', '1h')),
                current_candle_timestamp INTEGER,
                current_volume_usd REAL,
                avg_volume_9 REAL,
                rvol REAL,
                last_checked_at INTEGER,
                next_check_at INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                chart_json TEXT,
                PRIMARY KEY(pair_id, timeframe)
            );
            CREATE INDEX IF NOT EXISTS idx_rvol_due ON qualified_rvol(next_check_at);
            """
        )
        # Upgrade databases created before canonical-address recovery was added.
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(pairs)")}
        if "discovery_key" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE pairs ADD COLUMN discovery_key TEXT")
                self.conn.execute("UPDATE pairs SET discovery_key=lower(pair_address)")
                self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pair_discovery_key ON pairs(chain, discovery_key)")
        rvol_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(qualified_rvol)")}
        if "chart_json" not in rvol_columns:
            with self.conn:
                self.conn.execute("ALTER TABLE qualified_rvol ADD COLUMN chart_json TEXT")
        # Existing qualified pairs also need three RVOL schedules on upgrade.
        with self.conn:
            for timeframe in ("1m", "5m", "1h"):
                self.conn.execute(
                    """INSERT OR IGNORE INTO qualified_rvol(pair_id, timeframe)
                    SELECT id, ? FROM pairs WHERE status='QUALIFIED'""",
                    (timeframe,),
                )

    def close(self):
        self.conn.close()

    def add_discovered(self, chain: str, address: str, symbol: str) -> bool:
        if (chain, address.lower()) in self.ignored_pairs:
            return False
        now = now_ms()
        with self.conn:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO pairs
                (chain, discovery_key, pair_address, symbol, dexscreener_url, first_seen_at,
                 next_check_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chain, address.lower(), address, symbol or '…', f"https://dexscreener.com/{chain}/{address}", now, now, now, now),
            )
        return cursor.rowcount == 1

    def get_pair(self, pair_id: int):
        row = self.conn.execute("SELECT * FROM pairs WHERE id=?", (pair_id,)).fetchone()
        return dict(row) if row else None

    def get_pair_by_address(self, chain: str, address: str):
        row = self.conn.execute("SELECT * FROM pairs WHERE chain=? AND discovery_key=?", (chain, address.lower())).fetchone()
        return dict(row) if row else None

    def set_metadata(self, pair_id: int, pair: dict):
        now = now_ms()
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        with self.conn:
            self.conn.execute(
                """UPDATE pairs SET pair_address=?, token_address=?, quote_address=?, dex_id=?,
                symbol=?, name=?, pair_created_at=?, dexscreener_url=?,
                next_market_refresh_at=?, updated_at=? WHERE id=?""",
                (
                    pair.get("pairAddress"), base.get("address"), quote.get("address"), pair.get("dexId"),
                    base.get("symbol") or "…", base.get("name"), pair.get("pairCreatedAt"),
                    pair.get("url") or f"https://dexscreener.com/solana/{pair['pairAddress']}", now, now, pair_id,
                ),
            )

    def due_candidates(self, limit: int = 40):
        rows = self.conn.execute(
            """SELECT * FROM pairs WHERE status IN ('NEW', 'WATCHING_1M', 'WATCHING_5M',
            'WATCHING_1H', 'ERROR') AND next_check_at <= ?
            ORDER BY next_check_at ASC, id ASC LIMIT ?""",
            (now_ms(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def defer_candidate(self, pair_id: int, delay_seconds: float):
        with self.conn:
            self.conn.execute(
                "UPDATE pairs SET next_check_at=?, updated_at=? WHERE id=?",
                (now_ms() + int(delay_seconds * 1000), now_ms(), pair_id),
            )

    def record_candle(self, pair_id: int, timeframe: str, timestamp: int, volume: float, closed: bool):
        now = now_ms()
        with self.conn:
            self.conn.execute(
                """INSERT INTO candidate_candles
                (pair_id, timeframe, first_candle_timestamp, volume_usd, is_closed, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair_id, timeframe) DO UPDATE SET
                first_candle_timestamp=excluded.first_candle_timestamp,
                volume_usd=excluded.volume_usd, is_closed=excluded.is_closed,
                last_checked_at=excluded.last_checked_at""",
                (pair_id, timeframe, timestamp, volume, int(closed), now),
            )

    def set_candidate_state(self, pair_id: int, status: str, stage: str, delay_seconds: float):
        now = now_ms()
        with self.conn:
            self.conn.execute(
                """UPDATE pairs SET status=?, stage=?, next_check_at=?,
                error_count=0, last_error=NULL, updated_at=? WHERE id=?""",
                (status, stage, now + int(delay_seconds * 1000), now, pair_id),
            )

    def qualify(self, pair_id: int, trigger: str, volume: float):
        now = now_ms()
        with self.conn:
            self.conn.execute(
                """UPDATE pairs SET status='QUALIFIED', qualified_at=?,
                qualification_trigger=?, qualification_volume=?,
                next_market_refresh_at=?, error_count=0, last_error=NULL, updated_at=?
                WHERE id=?""",
                (now, trigger, volume, now, now, pair_id),
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO qualified_rvol(pair_id, timeframe) VALUES (?, ?)",
                [(pair_id, timeframe) for timeframe in ("1m", "5m", "1h")],
            )

    def fail(self, pair_id: int):
        row = self.get_pair(pair_id)
        if not row:
            return
        self.ignored_pairs.add((row["chain"], row["discovery_key"]))
        with self.conn:
            self._remove_pair(pair_id)

    def _remove_pair(self, pair_id: int):
        self.conn.execute("DELETE FROM candidate_candles WHERE pair_id=?", (pair_id,))
        self.conn.execute("DELETE FROM qualified_rvol WHERE pair_id=?", (pair_id,))
        self.conn.execute("DELETE FROM qualified_market_snapshot WHERE pair_id=?", (pair_id,))
        self.conn.execute("DELETE FROM pairs WHERE id=?", (pair_id,))

    def delete_qualified(self, pair_id: int) -> bool:
        row = self.conn.execute(
            "SELECT chain, discovery_key FROM pairs WHERE id=? AND status='QUALIFIED'", (pair_id,)
        ).fetchone()
        if not row:
            return False
        self.ignored_pairs.add((row["chain"], row["discovery_key"]))
        with self.conn:
            self._remove_pair(pair_id)
        return True

    def candidate_error(self, pair_id: int, error: str, delay_seconds: float):
        now = now_ms()
        with self.conn:
            self.conn.execute(
                """UPDATE pairs SET status='ERROR', error_count=error_count+1,
                last_error=?, next_check_at=?, updated_at=? WHERE id=?""",
                (error[:300], now + int(delay_seconds * 1000), now, pair_id),
            )

    def due_market(self, limit: int = 60):
        rows = self.conn.execute(
            """SELECT * FROM pairs WHERE status IN ('QUALIFIED', 'WATCHING_1M',
            'WATCHING_5M', 'WATCHING_1H', 'NEW', 'ERROR') AND dex_id IS NOT NULL
            AND (next_market_refresh_at IS NULL OR next_market_refresh_at <= ?)
            ORDER BY next_market_refresh_at LIMIT ?""", (now_ms(), limit)
        ).fetchall()
        return [dict(row) for row in rows]

    def set_market(self, pair_id: int, market: dict, next_delay: float):
        now = now_ms()
        volume = market.get("volume") or {}
        change = market.get("priceChange") or {}
        txns = (market.get("txns") or {}).get("m5") or {}
        liquidity = market.get("liquidity") or {}
        with self.conn:
            self.conn.execute(
                """INSERT INTO qualified_market_snapshot
                (pair_id, price_usd, market_cap, fdv, volume_m5, volume_h1,
                 volume_h24, liquidity_usd, price_change_5m, price_change_1h,
                 txns_m5, updated_at)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (SELECT 1 FROM pairs WHERE id=?)
                ON CONFLICT(pair_id) DO UPDATE SET
                price_usd=excluded.price_usd, market_cap=excluded.market_cap,
                fdv=excluded.fdv, volume_m5=excluded.volume_m5,
                volume_h1=excluded.volume_h1, volume_h24=excluded.volume_h24,
                liquidity_usd=excluded.liquidity_usd,
                price_change_5m=excluded.price_change_5m,
                price_change_1h=excluded.price_change_1h,
                txns_m5=excluded.txns_m5, updated_at=excluded.updated_at""",
                (
                    pair_id, float(market["priceUsd"]) if market.get("priceUsd") else None,
                    market.get("marketCap"), market.get("fdv"), volume.get("m5"),
                    volume.get("h1"), volume.get("h24"), liquidity.get("usd"),
                    change.get("m5"), change.get("h1"),
                    int(txns.get("buys", 0)) + int(txns.get("sells", 0)), now, pair_id,
                ),
            )
            self.conn.execute(
                "UPDATE pairs SET next_market_refresh_at=?, updated_at=? WHERE id=?",
                (now + int(next_delay * 1000), now, pair_id),
            )

    def defer_market(self, pair_ids: list[int], delay_seconds: float):
        next_at = now_ms() + int(delay_seconds * 1000)
        with self.conn:
            self.conn.executemany(
                "UPDATE pairs SET next_market_refresh_at=? WHERE id=?",
                [(next_at, pair_id) for pair_id in pair_ids],
            )

    def due_rvol(self, limit: int = 48):
        rows = self.conn.execute(
            """SELECT p.*, r.timeframe, r.rvol AS previous_rvol,
            r.avg_volume_9 AS previous_baseline FROM qualified_rvol r
            JOIN pairs p ON p.id=r.pair_id
            WHERE p.status='QUALIFIED' AND r.next_check_at <= ?
            ORDER BY r.next_check_at ASC, r.pair_id ASC LIMIT ?""",
            (now_ms(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_rvol(self, pair_id: int, timeframe: str, ratio, baseline, current_bar, delay_seconds: float, chart=None):
        now = now_ms()
        with self.conn:
            self.conn.execute(
                """UPDATE qualified_rvol SET rvol=?, avg_volume_9=?,
                current_candle_timestamp=?, current_volume_usd=?, last_checked_at=?,
                next_check_at=?, last_error=NULL, chart_json=? WHERE pair_id=? AND timeframe=?
                AND EXISTS (SELECT 1 FROM pairs WHERE id=? AND status='QUALIFIED')""",
                (ratio, baseline, current_bar.timestamp_ms if current_bar else None,
                 current_bar.volume_usd if current_bar else None, now,
                 now + int(delay_seconds * 1000), json.dumps(chart) if chart is not None else None,
                 pair_id, timeframe, pair_id),
            )

    def rvol_error(self, pair_id: int, timeframe: str, error: str, delay_seconds: float):
        with self.conn:
            self.conn.execute(
                """UPDATE qualified_rvol SET rvol=NULL, avg_volume_9=NULL,
                current_candle_timestamp=NULL, current_volume_usd=NULL,
                next_check_at=?, last_error=? WHERE pair_id=? AND timeframe=?""",
                (now_ms() + int(delay_seconds * 1000), error[:300], pair_id, timeframe),
            )

    def candidates(self, limit: int = 120):
        rows = self.conn.execute(
            """SELECT p.*, c1.volume_usd AS volume_1m, c5.volume_usd AS volume_5m,
            ch.volume_usd AS volume_1h, m.price_usd, m.market_cap, m.volume_h24,
            m.liquidity_usd
            FROM pairs p
            LEFT JOIN candidate_candles c1 ON c1.pair_id=p.id AND c1.timeframe='1m'
            LEFT JOIN candidate_candles c5 ON c5.pair_id=p.id AND c5.timeframe='5m'
            LEFT JOIN candidate_candles ch ON ch.pair_id=p.id AND ch.timeframe='1h'
            LEFT JOIN qualified_market_snapshot m ON m.pair_id=p.id
            WHERE p.status IN ('NEW', 'WATCHING_1M', 'WATCHING_5M', 'WATCHING_1H', 'ERROR')
            ORDER BY p.first_seen_at DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def qualified(self, limit: int = 300):
        rows = self.conn.execute(
            """SELECT p.*, m.price_usd, m.market_cap, m.fdv, m.volume_m5,
            m.volume_h1, m.volume_h24, m.liquidity_usd, m.price_change_5m,
            m.price_change_1h, m.txns_m5, m.updated_at AS market_updated_at,
            r1.rvol AS rvol_1m, r5.rvol AS rvol_5m, rh.rvol AS rvol_1h,
            r1.avg_volume_9 AS avg_vol_1m_9,
            r5.avg_volume_9 AS avg_vol_5m_9,
            rh.avg_volume_9 AS avg_vol_1h_9,
            r1.current_candle_timestamp AS rvol_1m_candle_timestamp,
            r5.current_candle_timestamp AS rvol_5m_candle_timestamp,
            rh.current_candle_timestamp AS rvol_1h_candle_timestamp,
            r1.chart_json
            FROM pairs p LEFT JOIN qualified_market_snapshot m ON m.pair_id=p.id
            LEFT JOIN qualified_rvol r1 ON r1.pair_id=p.id AND r1.timeframe='1m'
            LEFT JOIN qualified_rvol r5 ON r5.pair_id=p.id AND r5.timeframe='5m'
            LEFT JOIN qualified_rvol rh ON rh.pair_id=p.id AND rh.timeframe='1h'
            WHERE p.status='QUALIFIED' ORDER BY p.qualified_at DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [{**dict(row), "chart": json.loads(row["chart_json"] or "[]")} for row in rows]

    def counts(self):
        rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM pairs GROUP BY status").fetchall()
        result = {row["status"]: row["count"] for row in rows}
        result["active_candidates"] = sum(result.get(key, 0) for key in ("NEW", "WATCHING_1M", "WATCHING_5M", "WATCHING_1H", "ERROR"))
        return result
