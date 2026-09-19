"""Decoder for Dexscreener's version 1.0.0 chart-bars binary response.

The response uses Avro-style zigzag lengths and little-endian doubles. Each
bar contains four (native, USD) OHLC values, then USD volume. This is an
undocumented format; reject unfamiliar versions rather than misqualify pairs.
"""

from dataclasses import dataclass
import math
import struct


class CandleDecodeError(ValueError):
    pass


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open_usd: float
    high_usd: float
    low_usd: float
    close_usd: float
    volume_usd: float


def calculate_rvol(current_volume: float, previous_9_volumes: list[float]):
    """Use exactly the nine completed candles preceding the active candle."""
    if len(previous_9_volumes) != 9:
        return None
    values = [current_volume, *previous_9_volumes]
    if any(not math.isfinite(volume) or volume < 0 for volume in values):
        return None
    baseline = sum(previous_9_volumes) / 9
    if baseline <= 0:
        return None
    return current_volume / baseline


def current_rvol(bars: list[Candle], resolution: int, timestamp_ms: int):
    """Return (ratio, baseline, current bar) only for ten contiguous chart buckets."""
    duration_ms = resolution * 60_000
    active_start = timestamp_ms // duration_ms * duration_ms
    if len(bars) < 10 or bars[-1].timestamp_ms != active_start:
        return None, None, None
    window = bars[-10:]
    if any(bar.timestamp_ms != active_start - (9 - index) * duration_ms
           for index, bar in enumerate(window)):
        return None, None, None
    prior = [bar.volume_usd for bar in window[:-1]]
    ratio = calculate_rvol(window[-1].volume_usd, prior)
    return (ratio, sum(prior) / 9 if ratio is not None else None, window[-1])


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def long(self) -> int:
        unsigned = 0
        for shift in range(0, 70, 7):
            if self.pos >= len(self.data):
                raise CandleDecodeError("Truncated integer")
            byte = self.data[self.pos]
            self.pos += 1
            unsigned |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return (unsigned >> 1) ^ -(unsigned & 1)
        raise CandleDecodeError("Integer is too long")

    def string(self) -> str:
        length = self.long()
        if length < 0 or length > 1000 or self.pos + length > len(self.data):
            raise CandleDecodeError("Invalid string length")
        value = self.data[self.pos : self.pos + length].decode("utf-8")
        self.pos += length
        return value

    def double(self) -> float:
        if self.pos + 8 > len(self.data):
            raise CandleDecodeError("Truncated double")
        value = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return value


def decode_bars(data: bytes) -> list[Candle]:
    reader = _Reader(data)
    try:
        version = reader.string()
        if version != "1.0.0":
            raise CandleDecodeError(f"Unsupported chart format {version!r}")
        marker = reader.long()
        if marker != 1:
            raise CandleDecodeError(f"Unexpected chart marker {marker}")
        count = reader.long()
        if not 0 <= count <= 1000:
            raise CandleDecodeError(f"Invalid bar count {count}")
        bars = []
        for _ in range(count):
            timestamp_ms = int(reader.double())
            prices = []
            for _ in range(4):
                reader.string()  # native quote value
                usd_branch = reader.long()
                if usd_branch != 1:
                    raise CandleDecodeError("USD OHLC field unavailable")
                prices.append(float(reader.string()))
            volume_branch = reader.long()
            if volume_branch != 1:
                raise CandleDecodeError("USD volume field unavailable")
            volume_usd = float(reader.string())
            reader.double()  # additional chart metric
            reader.double()  # additional chart metric
            bars.append(Candle(timestamp_ms, *prices, volume_usd))
        if reader.pos != len(data):
            raise CandleDecodeError("Unexpected trailing chart bytes")
        if any(bars[index].timestamp_ms >= bars[index + 1].timestamp_ms for index in range(len(bars) - 1)):
            raise CandleDecodeError("Bars are not ordered from oldest to newest")
        return bars
    except (UnicodeDecodeError, ValueError, struct.error) as exc:
        if isinstance(exc, CandleDecodeError):
            raise
        raise CandleDecodeError(str(exc)) from exc
