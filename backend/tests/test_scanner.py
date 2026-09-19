import asyncio

from app.candles import Candle
from app.config import Settings
from app.db import Database, now_ms
from app.scanner import Scanner


PAIR = "6YX9meWTvAYRtCyTbxibAw6RgoXoyyeaCo8R7LMpump"
QUOTE = "So11111111111111111111111111111111111111112"


def metadata():
    return {
        "chainId": "solana",
        "pairAddress": PAIR,
        "dexId": "pumpswap",
        "baseToken": {"address": "B" * 32, "symbol": "TEST", "name": "Test token"},
        "quoteToken": {"address": QUOTE, "symbol": "SOL"},
        "url": f"https://dexscreener.com/solana/{PAIR}",
        "pairCreatedAt": now_ms() - 120_000,
    }


def candle(volume, age_ms=0):
    return Candle(
        timestamp_ms=now_ms() - age_ms,
        open_usd=1,
        high_usd=1,
        low_usd=1,
        close_usd=1,
        volume_usd=volume,
    )


def test_lowercase_discovery_address_resolves_canonical_and_qualifies(tmp_path):
    async def scenario():
        db = Database(tmp_path / "scanner.db")
        scanner = Scanner(Settings(), db)
        try:
            assert db.add_discovered("solana", PAIR.lower(), "TEST")
            assert not db.add_discovered("solana", PAIR, "TEST")
            pair_id = db.get_pair_by_address("solana", PAIR)["id"]

            async def search(discovery_key):
                assert discovery_key == PAIR.lower()
                return metadata()

            async def first_candle(pair, resolution):
                assert pair["pair_address"] == PAIR
                assert resolution == 1
                return candle(5_001)

            scanner.api.search_pair = search
            scanner.browser.first_candle = first_candle
            await scanner._evaluate(pair_id)

            pair = db.get_pair(pair_id)
            assert pair["status"] == "QUALIFIED"
            assert pair["qualification_trigger"] == "1m"
            assert pair["qualification_volume"] == 5_001
            assert pair["pair_address"] == PAIR
            assert len(db.qualified()) == 1
            assert not db.candidates()  # Qualified rows leave the active candidate table.
        finally:
            await scanner.api.close()
            db.close()

    asyncio.run(scenario())


def test_closed_candles_advance_then_fail(tmp_path):
    async def scenario():
        db = Database(tmp_path / "scanner.db")
        scanner = Scanner(Settings(), db)
        try:
            db.add_discovered("solana", PAIR.lower(), "TEST")
            pair_id = db.get_pair_by_address("solana", PAIR)["id"]

            async def search(_):
                return metadata()

            volumes = {1: 4_999, 5: 9_999, 60: 99_999}

            async def first_candle(_, resolution):
                return candle(volumes[resolution], age_ms=3_700_000)

            scanner.api.search_pair = search
            scanner.browser.first_candle = first_candle
            await scanner._evaluate(pair_id)
            assert db.get_pair(pair_id)["stage"] == "5m"
            await scanner._evaluate(pair_id)
            assert db.get_pair(pair_id)["stage"] == "1h"
            await scanner._evaluate(pair_id)
            assert db.get_pair(pair_id) is None
            assert not db.candidates()
            assert not db.qualified()
            assert not db.add_discovered("solana", PAIR, "TEST")
        finally:
            await scanner.api.close()
            db.close()

    asyncio.run(scenario())
