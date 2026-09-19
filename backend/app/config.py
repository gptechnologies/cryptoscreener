from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


def _number(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    chain: str = os.getenv("CHAIN", "solana")
    discovery_url: str = os.getenv(
        "DEXSCREENER_NEW_PAIRS_URL",
        "https://dexscreener.com/new-pairs/solana?rankBy=pairAge&order=asc",
    )
    database_path: str = os.getenv("DATABASE_PATH", ":memory:")
    browser_challenge_timeout: float = _number("BROWSER_CHALLENGE_TIMEOUT_SECONDS", 90)
    discovery_interval: float = _number("DISCOVERY_INTERVAL_SECONDS", 1.5)
    thresholds: tuple = (
        _number("ONE_MIN_VOLUME_THRESHOLD", 5000),
        _number("FIVE_MIN_VOLUME_THRESHOLD", 10000),
        _number("ONE_HOUR_VOLUME_THRESHOLD", 100000),
    )
    candidate_intervals: tuple = (
        _number("ONE_MIN_POLL_SECONDS", 3),
        _number("FIVE_MIN_POLL_SECONDS", 5),
        _number("ONE_HOUR_POLL_SECONDS", 15),
    )
    qualified_refresh: float = _number("QUALIFIED_REFRESH_SECONDS", 5)
    rvol_intervals: tuple = (
        _number("RVOL_1M_POLL_SECONDS", 6),
        _number("RVOL_5M_POLL_SECONDS", 15),
        _number("RVOL_1H_POLL_SECONDS", 30),
    )
    chart_rate: float = _number("CHART_REQUESTS_PER_SECOND", 3)
    chart_concurrency: int = int(_number("CHART_CONCURRENCY", 2))
    api_rate: float = _number("API_REQUESTS_PER_SECOND", 4)


settings = Settings()
