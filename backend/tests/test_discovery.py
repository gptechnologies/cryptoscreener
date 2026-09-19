import json

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


def test_browser_socket_frames_feed_one_queue_without_dom_polling():
    class Socket:
        url = "wss://io.dexscreener.com/dex/screener/v7/pairs/h24/1"

        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    source = BrowserSource(Settings())
    socket = Socket()
    source._on_websocket(socket)
    socket.handlers["framereceived"](json.dumps({"pairs": [
        {"chainId": "solana", "pairAddress": PAIR, "baseToken": {"symbol": "TEST"}},
    ]}))
    assert source.socket_connected
    assert source.socket_pairs_seen
    assert source.discovery_queue.get_nowait() == {"address": PAIR, "symbol": "TEST"}
    socket.handlers["close"](socket)
    assert not source.socket_connected
