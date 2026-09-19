import asyncio
import json

import pytest

from app.db import Database
from app.discovery import pairs_from_frame
from app.dexscreener import BrowserSource
from app.config import Settings


PAIR = "6YX9meWTvAYRtCyTbxibAw6RgoXoyyeaCo8R7LMpump"


def test_socket_frame_extracts_only_solana_pairs():
    frame = {"type": "pairs", "data": {"pairs": [
        {"chainId": "solana", "pairAddress": PAIR, "baseToken": {"symbol": "TEST"}},
        {"chainId": "ethereum", "pairAddress": PAIR, "baseToken": {"symbol": "ETH"}},
        {"chainId": "solana", "pairAddress": "not-an-address"},
    ]}}
    assert pairs_from_frame(json.dumps(frame)) == [{"address": PAIR, "symbol": "TEST"}]
    assert pairs_from_frame(b"not json") == []
    assert pairs_from_frame(b"\xff") == []


def test_delete_qualified_ignores_repeated_discovery_and_clears_chart():
    db = Database()
    try:
        assert db.add_discovered("solana", PAIR, "TEST")
        pair_id = db.get_pair_by_address("solana", PAIR)["id"]
        db.qualify(pair_id, "1m", 5001)
        db.set_rvol(pair_id, "1m", 2, 100, None, 3,
                    [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}])
        assert len(db.qualified()[0]["chart"]) == 1
        assert db.delete_qualified(pair_id)
        assert not db.delete_qualified(pair_id)
        assert not db.add_discovered("solana", PAIR, "TEST")
        assert db.qualified() == []
        assert db.due_rvol() == []
        db.set_market(pair_id, {"priceUsd": "1"}, 5)
        assert db.conn.execute("SELECT COUNT(*) FROM qualified_market_snapshot").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("socket_url", [
    "wss://io.dexscreener.com/dex/screener/v7/pairs/h24/1?rank=age",
    "wss://io.dexscreener.com/dex/screener/pairs/h24/1?rank=age",
])
def test_browser_socket_frames_feed_one_queue_without_dom_polling(socket_url):
    class Socket:
        def __init__(self):
            self.url = socket_url
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    async def scenario():
        source = BrowserSource(Settings())
        socket = Socket()
        source._on_websocket(socket)
        socket.handlers["framereceived"](json.dumps({"pairs": [
            {"chainId": "solana", "pairAddress": PAIR, "baseToken": {"symbol": "TEST"}},
        ]}))
        await asyncio.sleep(0)
        assert source.socket_connected
        assert source.socket_pairs_seen
        assert source.last_socket_frame_at is not None
        assert source.last_socket_url == socket_url.split("?", 1)[0]
        assert source.discovery_queue.get_nowait() == {"address": PAIR, "symbol": "TEST"}
        socket.handlers["close"](socket)
        assert not source.socket_connected

    asyncio.run(scenario())


def test_unrelated_socket_is_observed_but_not_selected():
    class Socket:
        url = "wss://example.com/dex/screener/v7/pairs/h24/1?secret=removed"

        def on(self, *_):
            raise AssertionError("Unrelated sockets must not get frame handlers")

    async def scenario():
        source = BrowserSource(Settings())
        source._on_websocket(Socket())
        assert not source.socket_connected
        assert source.observed_socket_urls == {"wss://example.com/dex/screener/v7/pairs/h24/1"}

    asyncio.run(scenario())


def test_cloudflare_challenge_detection_and_safe_diagnostics():
    async def scenario():
        source = BrowserSource(Settings())
        assert source.is_challenge_page(
            "Just a moment...",
            "https://dexscreener.com/new-pairs/solana?__cf_chl_rt_tk=secret",
        )
        assert source.is_challenge_page(
            "DEX Screener",
            "https://dexscreener.com/new-pairs/solana?__cf_chl_rt_tk=secret",
        )
        assert not source.is_challenge_page(
            "DEX Screener",
            "https://dexscreener.com/new-pairs/solana?rankBy=pairAge",
        )
        assert source._safe_url(
            "wss://io.dexscreener.com/dex/screener/pairs/h24/1?token=secret"
        ) == "wss://io.dexscreener.com/dex/screener/pairs/h24/1"

    asyncio.run(scenario())
