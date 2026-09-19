import asyncio
import sqlite3

import pytest

from app.candles import Candle, calculate_rvol, current_rvol
from app.config import Settings
from app.db import Database, now_ms
from app import scanner as scanner_module
from app.scanner import Scanner


PAIR = "6YX9meWTvAYRtCyTbxibAw6RgoXoyyeaCo8R7LMpump"


def bar(timestamp, volume):
    return Candle(timestamp, 1, 1, 1, 1, volume)


def window(resolution, timestamp, previous=None, active_volume=25):
    previous = previous if previous is not None else list(range(1, 10))
    duration = resolution * 60_000
    start = timestamp // duration * duration
    return [bar(start - (9 - index) * duration, volume)
            for index, volume in enumerate(previous)] + [bar(start, active_volume)]


@pytest.mark.parametrize("resolution", [1, 5, 60])
def test_current_candle_uses_exact_previous_nine_and_rolls(resolution):
    duration = resolution * 60_000
    timestamp = 1_800_000_123_456
    bars = window(resolution, timestamp)
    ratio, baseline, current = current_rvol(bars, resolution, timestamp)
    assert baseline == 5.0
    assert ratio == 5.0  # Current 25 / mean(1..9), excluding current.
    assert current.volume_usd == 25

    next_timestamp = bars[-1].timestamp_ms + duration + 500
    bars.append(bar(bars[-1].timestamp_ms + duration, 11))
    ratio, baseline, current = current_rvol(bars, resolution, next_timestamp)
    assert baseline == pytest.approx((sum(range(2, 10)) + 25) / 9)
    assert ratio == pytest.approx(11 / baseline)
    assert current.volume_usd == 11


def test_insufficient_missing_stale_and_zero_baselines():
    timestamp = 1_800_000_123_456
    bars = window(1, timestamp)
    assert calculate_rvol(10, [1] * 8) is None
    assert calculate_rvol(10, [0] * 9) is None
    assert current_rvol(bars[:-1], 1, timestamp) == (None, None, None)
    assert current_rvol(bars, 1, timestamp + 60_000) == (None, None, None)
    gap = bars.copy()
    gap[3] = bar(gap[3].timestamp_ms + 1, gap[3].volume_usd)
    assert current_rvol(gap, 1, timestamp) == (None, None, None)
    zeros = window(1, timestamp, [0] * 9)
    assert current_rvol(zeros, 1, timestamp) == (None, None, bar(zeros[-1].timestamp_ms, 25))


def test_raw_rvol_snapshot_and_three_timeframe_schedule(tmp_path, monkeypatch):
    fixed_now = now_ms()
    monkeypatch.setattr(scanner_module, "now_ms", lambda: fixed_now)

    async def scenario():
        db = Database(tmp_path / "scanner.db")
        scanner = Scanner(Settings(), db)
        try:
            db.add_discovered("solana", PAIR.lower(), "TEST")
            pair = db.get_pair_by_address("solana", PAIR)
            db.set_metadata(pair["id"], {
                "pairAddress": PAIR, "dexId": "pumpswap",
                "baseToken": {"address": "B" * 32, "symbol": "TEST"},
                "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
            })
            db.qualify(pair["id"], "1m", 5001.0)
            due = db.due_rvol()
            assert {row["timeframe"] for row in due} == {"1m", "5m", "1h"}

            async def bars(_, resolution):
                return window(resolution, fixed_now, active_volume=25.0)

            scanner.browser.bars = bars
            for row in due:
                await scanner._update_rvol(row)
            item = db.qualified()[0]
            for timeframe in ("1m", "5m", "1h"):
                assert item[f"rvol_{timeframe}"] == 5.0
                assert item[f"avg_vol_{timeframe}_9"] == 5.0
                assert isinstance(item[f"rvol_{timeframe}"], float)

            async def updated_bars(_, resolution):
                return window(resolution, fixed_now, active_volume=50.0)

            scanner.browser.bars = updated_bars
            await scanner._update_rvol({**due[0], "previous_rvol": 5.0, "previous_baseline": 5.0})
            assert db.qualified()[0][f"rvol_{due[0]['timeframe']}"] == 10.0

            db.rvol_error(pair["id"], "1m", "temporary failure", 30)
            assert db.qualified()[0]["rvol_1m"] is None
            assert db.qualified()[0]["rvol_5m"] == 5.0
        finally:
            await scanner.api.close()
            db.close()

    asyncio.run(scenario())


def test_existing_qualified_pair_gets_rvol_schedule_on_upgrade(tmp_path):
    path = tmp_path / "scanner.db"
    db = Database(path)
    db.add_discovered("solana", PAIR, "TEST")
    pair_id = db.get_pair_by_address("solana", PAIR)["id"]
    db.qualify(pair_id, "1m", 5001.0)
    db.close()

    # Simulate a database created before the RVOL table existed.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE qualified_rvol")

    upgraded = Database(path)
    try:
        assert {row["timeframe"] for row in upgraded.due_rvol()} == {"1m", "5m", "1h"}
        assert upgraded.qualified()[0]["rvol_1m"] is None
    finally:
        upgraded.close()
