import base64

import pytest

from app.candles import CandleDecodeError, decode_bars


# Captured from Dexscreener's 1m market-cap chart for a Solana PumpSwap pair.
# Selecting its first candle in the chart displayed Volume 6.93K.
SAMPLE = (
    "CjEuMC4wAgYAAKhBLQt6QhIzMTEwLjMzMDkCFjMyNjI3My43MjExEjUzNTAuNjczMgIWNTYxMjg1LjYyMTESNTIyMi4zNTEzAhY1NDc4MjQuNjUxNRI1MzUwLjY3MzICFjU2MTI4NS42MjExAg42OTI5LjU5AAAAgaGzukEAAACBobO6QQAATlAtC3pCEjUzNTAuNjczMgIWNTYxMjg1LjYyMTESNTUzMi41NDg0AhY1ODAzNjQuMzI5MhI1MDY4Ljk3NzUCFjUzMTczNS43NDA2FDUxMjMuMDc2MzkCFjUzNzQxMC43MTM5AhAxMjQ3MC44NQAAAJihs7pBAAAAmKGzukEAAPReLQt6QhQ1MTIzLjA3NjM5AhY1Mzc0MTAuNzEzORI1MzU2LjgxMTYCFjU2MTkyOS41NDEzEjQ5ODQuNTMxNgIWNTIyODc3LjM2NzMSNTMyMi4zNzMyAhY1NTgzMTYuOTU2NQIOODgwOC4zNwAAAE+is7pBAAAAT6KzukE="
)


def test_real_chart_payload_has_ordered_usd_candles():
    bars = decode_bars(base64.b64decode(SAMPLE))
    assert len(bars) == 3
    assert [bar.volume_usd for bar in bars] == [6929.59, 12470.85, 8808.37]
    assert bars[0].timestamp_ms == 1789706640000
    assert bars[0].open_usd == 326273.7211
    assert bars[0].high_usd == 561285.6211


def test_decoder_rejects_changed_format():
    payload = base64.b64decode(SAMPLE).replace(b"1.0.0", b"2.0.0", 1)
    with pytest.raises(CandleDecodeError, match="Unsupported chart format"):
        decode_bars(payload)
