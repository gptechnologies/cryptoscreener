import asyncio
import base64
import logging
import re
from typing import Optional
from urllib.parse import urlencode

import httpx
from playwright.async_api import async_playwright

from .candles import CandleDecodeError, decode_bars
from .config import Settings
from .db import now_ms
from .discovery import pairs_from_frame


logger = logging.getLogger(__name__)
PAIR_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self.spacing = 1 / max(requests_per_second, 0.1)
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        loop = asyncio.get_running_loop()
        async with self.lock:
            now = loop.time()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.spacing
        if delay:
            await asyncio.sleep(delay)


class PublicApi:
    def __init__(self, settings: Settings):
        self.limiter = RateLimiter(settings.api_rate)
        self.client = httpx.AsyncClient(base_url="https://api.dexscreener.com", timeout=12)

    async def close(self):
        await self.client.aclose()

    async def pairs(self, chain: str, addresses: list[str]) -> list[dict]:
        if not addresses:
            return []
        await self.limiter.wait()
        response = await self.client.get(f"/latest/dex/pairs/{chain}/{','.join(addresses)}")
        response.raise_for_status()
        return response.json().get("pairs") or []

    async def search_pair(self, discovery_key: str) -> Optional[dict]:
        await self.limiter.wait()
        response = await self.client.get("/latest/dex/search", params={"q": discovery_key})
        response.raise_for_status()
        return next(
            (item for item in response.json().get("pairs") or []
             if item.get("chainId") == "solana" and item.get("pairAddress", "").lower() == discovery_key.lower()),
            None,
        )


class BrowserSource:
    """One browser, one context and one page for discovery and chart requests."""

    EXTRACT_ROWS = """() => {
      const output = [];
      const seen = new Set();
      for (const anchor of document.querySelectorAll('a[href]')) {
        const path = new URL(anchor.href).pathname;
        const match = path.match(/^\\/solana\\/([1-9A-HJ-NP-Za-km-z]{32,44})$/);
        if (!match || seen.has(match[1])) continue;
        const body = anchor.innerText || '';
        if (!body.includes('/') || body.length < 20) continue;
        const specific = anchor.querySelector('[class*="base-token-symbol"]');
        const generic = body.match(/([^\\s/]+)\\s*\\/\\s*[^\\s]+/);
        output.push({address: match[1], symbol: (specific?.textContent || generic?.[1] || '…').trim()});
        seen.add(match[1]);
      }
      return output;
    }"""

    FETCH_BYTES = """async (url) => {
      const response = await fetch(url, { credentials: 'include', cache: 'no-store' });
      if (!response.ok) return { status: response.status, body: '' };
      const bytes = new Uint8Array(await response.arrayBuffer());
      let raw = '';
      for (let i = 0; i < bytes.length; i += 8192) {
        raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
      }
      return { status: response.status, body: btoa(raw) };
    }"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.socket = None
        self.socket_connected = False
        self.last_socket_frame_at = None
        self.discovery_queue = asyncio.Queue(maxsize=1000)
        self.started_at = None
        self.last_dom_fallback_at = 0
        self.socket_pairs_seen = False
        self.chart_limiter = RateLimiter(settings.chart_rate)
        self.chart_slots = asyncio.Semaphore(settings.chart_concurrency)

    async def start(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-extensions", "--disable-background-networking",
                      "--disable-default-apps", "--disable-sync", "--disable-translate",
                      "--mute-audio"],
            )
            self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=False)
            self.page = await self.context.new_page()
            await self.page.route("**/*", self._route)
            self.page.on("websocket", self._on_websocket)
            self.browser.on("disconnected", self._on_browser_disconnect)
            self.started_at = now_ms()
            await self.page.goto(self.settings.discovery_url, wait_until="domcontentloaded", timeout=30000)
            logger.info("BROWSER_STARTED url=%s", self.page.url)
        except Exception:
            await self.close()
            raise

    async def _route(self, route):
        if route.request.resource_type in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()

    def _on_browser_disconnect(self, _):
        self.socket_connected = False
        logger.warning("BROWSER_DISCONNECTED")

    def _on_websocket(self, socket):
        if "/dex/screener/v7/pairs/" not in socket.url:
            return
        self.socket = socket
        self.socket_connected = True
        self.last_socket_frame_at = now_ms()
        logger.info("DISCOVERY_CONNECTED")
        socket.on("framereceived", self._on_frame)
        socket.on("close", lambda _: self._on_socket_close(socket))
        socket.on("socketerror", lambda _: self._on_socket_close(socket))

    def _on_socket_close(self, socket):
        if self.socket is socket:
            self.socket_connected = False

    def _on_frame(self, payload):
        self.last_socket_frame_at = now_ms()
        pairs = pairs_from_frame(payload)
        if pairs:
            self.socket_pairs_seen = True
        for pair in pairs:
            if self.discovery_queue.full():
                self.discovery_queue.get_nowait()
            self.discovery_queue.put_nowait(pair)

    async def close(self):
        if self.context:
            try:
                await asyncio.wait_for(self.context.close(), timeout=5)
            except Exception as exc:
                logger.warning("PLAYWRIGHT_CLOSE_ERROR error=%s", exc)
        if self.browser:
            try:
                await asyncio.wait_for(self.browser.close(), timeout=5)
            except Exception as exc:
                logger.warning("BROWSER_CLOSE_ERROR error=%s", exc)
        if self.playwright:
            try:
                await asyncio.wait_for(self.playwright.stop(), timeout=5)
            except Exception as exc:
                logger.warning("PLAYWRIGHT_STOP_ERROR error=%s", exc)
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None
        self.socket = None
        self.socket_connected = False
        self.started_at = None
        self.last_socket_frame_at = None
        self.socket_pairs_seen = False
        self.discovery_queue = asyncio.Queue(maxsize=1000)

    async def discover(self) -> list[dict]:
        if not self.browser or not self.browser.is_connected() or not self.page or self.page.is_closed():
            raise RuntimeError("Discovery browser is closed")
        now = now_ms()
        if not self.socket_connected and now - self.started_at > 30_000:
            raise RuntimeError("Dexscreener discovery socket did not connect")
        if self.socket_connected and self.last_socket_frame_at and now - self.last_socket_frame_at > 60_000:
            raise RuntimeError("Dexscreener discovery socket is silent for 60 seconds")
        rows = []
        try:
            rows.append(await asyncio.wait_for(self.discovery_queue.get(), timeout=2))
        except asyncio.TimeoutError:
            pass
        while not self.discovery_queue.empty():
            rows.append(self.discovery_queue.get_nowait())
        if rows:
            return rows
        # Development fallback when the undocumented socket envelope changes.
        if not self.socket_pairs_seen and now - self.started_at > 15_000 and now - self.last_dom_fallback_at > 10_000:
            self.last_dom_fallback_at = now
            return await self.page.evaluate(self.EXTRACT_ROWS)
        return []

    @staticmethod
    def _chart_dexes(dex_id: str) -> list[str]:
        if dex_id in ("pumpfun", "pumpswap"):
            return ["pumpfundex"]
        return [dex_id]

    async def bars(self, pair: dict, resolution: int):
        if not self.page or self.page.is_closed():
            raise RuntimeError("Discovery browser is unavailable")
        dex_id = pair.get("dex_id")
        quote = pair.get("quote_address")
        address = pair.get("pair_address")
        if not dex_id or not quote or not address or not PAIR_ADDRESS.fullmatch(address):
            raise RuntimeError("Pair metadata is incomplete")
        async with self.chart_slots:
            last_error = None
            for chart_dex in self._chart_dexes(dex_id):
                query = urlencode({"mc": 1, "res": resolution, "cb": 329, "q": quote, "uo": 0})
                url = f"https://io.dexscreener.com/dex/chart/amm/v3/{chart_dex}/bars/solana/{address}?{query}"
                await self.chart_limiter.wait()
                result = await self.page.evaluate(self.FETCH_BYTES, url)
                if result["status"] != 200:
                    last_error = f"Chart endpoint returned HTTP {result['status']} ({chart_dex})"
                    continue
                try:
                    bars = decode_bars(base64.b64decode(result["body"], validate=True))
                except (ValueError, CandleDecodeError) as exc:
                    last_error = f"Chart decode failed: {exc}"
                    continue
                return bars
            raise RuntimeError(last_error or "No chart source succeeded")

    async def first_candle(self, pair: dict, resolution: int):
        bars = await self.bars(pair, resolution)
        return bars[0] if bars else None
