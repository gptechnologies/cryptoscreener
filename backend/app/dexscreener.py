import asyncio
import base64
import logging
import re
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

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
        self.socket_connected_at = None
        self.last_socket_frame_at = None
        self.discovery_queue = asyncio.Queue(maxsize=1000)
        self.started_at = None
        self.last_dom_fallback_at = 0
        self.socket_pairs_seen = False
        self.challenge_detected = False
        self.last_page_title = None
        self.last_page_url = None
        self.last_socket_url = None
        self.observed_socket_urls: set[str] = set()
        self.restart_count = 0
        self.resource_blocking_enabled = False
        self.resource_route_task = None
        self.chart_limiter = RateLimiter(settings.chart_rate)
        self.chart_slots = asyncio.Semaphore(settings.chart_concurrency)

    async def start(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"],
            )
            self.restart_count += 1
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                accept_downloads=False,
                locale="en-US",
                timezone_id="America/New_York",
            )
            self.page = await self.context.new_page()
            self.page.on("websocket", self._on_websocket)
            self.page.on("pageerror", lambda error: logger.warning("DISCOVERY_PAGE_ERROR error=%s", error))
            self.browser.on("disconnected", self._on_browser_disconnect)
            self.started_at = now_ms()
            self.challenge_detected = False
            self.resource_blocking_enabled = False
            logger.info("BROWSER_STARTED attempt=%s", self.restart_count)
            await self.page.goto(
                self.settings.discovery_url,
                wait_until="domcontentloaded",
                timeout=int(self.settings.browser_challenge_timeout * 1000),
            )
            await self._wait_for_application()
            logger.info("DISCOVERY_PAGE_READY title=%s url=%s", self.last_page_title, self.last_page_url)
        except Exception:
            await self.close()
            raise

    @staticmethod
    def _safe_url(value: str) -> str:
        """Remove query strings so challenge tokens never enter logs or health."""
        try:
            parts = urlsplit(value)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except ValueError:
            return value.split("?", 1)[0]

    @staticmethod
    def is_challenge_page(title: str, url: str) -> bool:
        lowered = (title or "").lower()
        return (
            "just a moment" in lowered
            or "attention required" in lowered
            or "__cf_chl_" in (url or "")
            or "/cdn-cgi/challenge" in (url or "")
        )

    async def _wait_for_application(self):
        deadline = asyncio.get_running_loop().time() + self.settings.browser_challenge_timeout
        challenge_logged = False
        while True:
            self.last_page_title = await self.page.title()
            self.last_page_url = self._safe_url(self.page.url)
            challenged = self.is_challenge_page(self.last_page_title, self.page.url)
            self.challenge_detected = challenged
            if not challenged:
                return
            if not challenge_logged:
                logger.warning(
                    "DISCOVERY_CHALLENGE_DETECTED title=%s url=%s",
                    self.last_page_title,
                    self.last_page_url,
                )
                challenge_logged = True
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(
                    f"Dexscreener Cloudflare challenge did not clear within "
                    f"{self.settings.browser_challenge_timeout:g} seconds"
                )
            await asyncio.sleep(2)

    async def _route(self, route):
        if route.request.resource_type in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()

    async def _enable_resource_blocking(self):
        if self.resource_blocking_enabled or not self.page or self.page.is_closed():
            return
        await self.page.route("**/*", self._route)
        self.resource_blocking_enabled = True
        logger.info("DISCOVERY_RESOURCE_BLOCKING_ENABLED")

    def _on_browser_disconnect(self, _):
        self.socket_connected = False
        logger.warning("BROWSER_DISCONNECTED")

    def _on_websocket(self, socket):
        safe_url = self._safe_url(socket.url)
        if safe_url not in self.observed_socket_urls:
            self.observed_socket_urls.add(safe_url)
            logger.info("WEBSOCKET_OPENED url=%s", safe_url)
        parsed = urlsplit(socket.url)
        is_pairs_socket = (
            parsed.hostname == "io.dexscreener.com"
            and "/dex/screener/" in parsed.path
            and "/pairs/" in parsed.path
        )
        if not is_pairs_socket:
            return
        self.socket = socket
        self.socket_connected = True
        self.socket_connected_at = now_ms()
        self.last_socket_frame_at = None
        self.last_socket_url = safe_url
        self.challenge_detected = False
        logger.info("DISCOVERY_CONNECTED url=%s", safe_url)
        socket.on("framereceived", self._on_frame)
        socket.on("close", lambda _: self._on_socket_close(socket))
        socket.on("socketerror", lambda _: self._on_socket_close(socket))
        if not self.resource_route_task or self.resource_route_task.done():
            self.resource_route_task = asyncio.create_task(self._enable_resource_blocking())

    def _on_socket_close(self, socket):
        if self.socket is socket:
            self.socket_connected = False
            self.socket_connected_at = None

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
        if self.resource_route_task and not self.resource_route_task.done():
            self.resource_route_task.cancel()
            try:
                await self.resource_route_task
            except asyncio.CancelledError:
                pass
        self.resource_route_task = None
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
        self.socket_connected_at = None
        self.started_at = None
        self.last_socket_frame_at = None
        self.socket_pairs_seen = False
        self.resource_blocking_enabled = False
        self.discovery_queue = asyncio.Queue(maxsize=1000)

    async def discover(self) -> list[dict]:
        if not self.browser or not self.browser.is_connected() or not self.page or self.page.is_closed():
            raise RuntimeError("Discovery browser is closed")
        now = now_ms()
        if not self.socket_connected and now - self.started_at > 30_000:
            raise RuntimeError("Dexscreener discovery socket did not connect")
        if self.socket_connected:
            last_activity = self.last_socket_frame_at or self.socket_connected_at
            if last_activity and now - last_activity > 60_000:
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
