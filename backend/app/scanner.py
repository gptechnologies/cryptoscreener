import asyncio
from contextlib import suppress
import logging

from .candles import current_rvol
from .config import Settings
from .db import Database, now_ms
from .dexscreener import BrowserSource, PublicApi


logger = logging.getLogger(__name__)
STAGES = ("1m", "5m", "1h")
RESOLUTIONS = {"1m": 1, "5m": 5, "1h": 60}
DURATIONS_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}
STATUS = {"1m": "WATCHING_1M", "5m": "WATCHING_5M", "1h": "WATCHING_1H"}


class Events:
    def __init__(self):
        self.listeners: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=32)
        self.listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        self.listeners.discard(queue)

    def publish(self, kind: str):
        for queue in tuple(self.listeners):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(kind)


class Scanner:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.api = PublicApi(settings)
        self.browser = BrowserSource(settings)
        self.events = Events()
        self.tasks: list[asyncio.Task] = []
        self.worker_tasks: set[asyncio.Task] = set()
        self.inflight: set[int] = set()
        self.rvol_inflight: set[tuple[int, str]] = set()
        self.browser_connected = False
        self.discovery_rows = 0
        self.last_successful_scan = None
        self.last_pair_seen = None
        self.last_chart_success = None
        self.last_market_success = None
        self.last_error = None
        self.started_at = now_ms()
        self._last_discovery_warning_at = 0

    def start(self):
        self.tasks = [
            asyncio.create_task(self.discovery_loop(), name="discovery"),
            asyncio.create_task(self.candidate_loop(), name="candidates"),
            asyncio.create_task(self.market_loop(), name="market"),
            asyncio.create_task(self.rvol_loop(), name="rvol"),
        ]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for task in self.worker_tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        for task in tuple(self.worker_tasks):
            with suppress(asyncio.CancelledError):
                await task
        await self.browser.close()
        await self.api.close()

    async def discovery_loop(self):
        delays = (2, 5, 10, 20, 30)
        attempt = 0
        while True:
            try:
                if not self.browser.page or self.browser.page.is_closed():
                    await self.browser.close()
                    await self.browser.start()
                rows = await self.browser.discover()
                self.browser_connected = bool(self.browser.browser and self.browser.browser.is_connected())
                self.discovery_rows = len(rows)
                if self.browser.socket_connected:
                    self.last_successful_scan = self.browser.last_socket_frame_at
                recovered = self.last_error is not None
                self.last_error = None
                attempt = 0
                if recovered:
                    logger.info("DISCOVERY_RECOVERED rows=%s", len(rows))
                    self.events.publish("health")
                for row in rows:
                    if self.db.add_discovered(self.settings.chain, row["address"], row["symbol"]):
                        self.last_pair_seen = now_ms()
                        logger.info("DISCOVERY_PAIR_FOUND pair=%s symbol=%s", row["address"], row["symbol"])
                        self.events.publish("candidate_added")
                await asyncio.sleep(self.settings.discovery_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"Discovery: {exc}"
                changed = error != self.last_error
                self.last_error = error
                self.browser_connected = False
                timestamp = now_ms()
                if changed or timestamp - self._last_discovery_warning_at >= 30_000:
                    logger.warning("DISCOVERY_ERROR error=%s", exc)
                    self._last_discovery_warning_at = timestamp
                if changed:
                    self.events.publish("health")
                await self.browser.close()
                await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
                attempt += 1

    async def candidate_loop(self):
        while True:
            try:
                capacity = max(0, 24 - len(self.inflight))
                if capacity:
                    for pair in self.db.due_candidates(limit=capacity + len(self.inflight)):
                        pair_id = pair["id"]
                        if pair_id in self.inflight:
                            continue
                        self.inflight.add(pair_id)
                        task = asyncio.create_task(self._evaluate(pair_id), name=f"candidate-{pair_id}")
                        self.worker_tasks.add(task)
                        task.add_done_callback(lambda finished, pid=pair_id: self._finish_candidate(pid, finished))
                        capacity -= 1
                        if capacity <= 0:
                            break
                await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CANDIDATE_LOOP_ERROR")
                await asyncio.sleep(2)

    def _finish_candidate(self, pair_id: int, task: asyncio.Task):
        self.inflight.discard(pair_id)
        self.worker_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.error("CANDIDATE_TASK_ERROR pair_id=%s error=%s", pair_id, error)

    async def _evaluate(self, pair_id: int):
        pair = self.db.get_pair(pair_id)
        if not pair or pair["status"] in ("QUALIFIED", "FAILED"):
            return
        stage = pair["stage"]
        index = STAGES.index(stage)
        try:
            if not pair["dex_id"] or not pair["quote_address"]:
                exact = await self.api.search_pair(pair["discovery_key"])
                if not exact:
                    raise RuntimeError("Pair is not yet available in the public API")
                self.db.set_metadata(pair_id, exact)
                pair = self.db.get_pair(pair_id)
                self.events.publish("candidate_updated")
            bar = await self.browser.first_candle(pair, RESOLUTIONS[stage])
            if bar is None:
                self.db.set_candidate_state(pair_id, STATUS[stage], stage, self.settings.candidate_intervals[index])
                return
            self.last_chart_success = now_ms()
            is_closed = now_ms() >= bar.timestamp_ms + DURATIONS_MS[stage] + 2500
            self.db.record_candle(pair_id, stage, bar.timestamp_ms, bar.volume_usd, is_closed)
            if bar.volume_usd >= self.settings.thresholds[index]:
                self.db.qualify(pair_id, stage, bar.volume_usd)
                logger.info("PAIR_QUALIFIED pair=%s trigger=%s volume_usd=%.2f", pair["pair_address"], stage, bar.volume_usd)
                self.events.publish("qualified_added")
                return
            if is_closed:
                if index == len(STAGES) - 1:
                    self.db.fail(pair_id)
                    logger.info("PAIR_FAILED pair=%s", pair["pair_address"])
                    self.events.publish("candidate_removed")
                else:
                    next_stage = STAGES[index + 1]
                    self.db.set_candidate_state(pair_id, STATUS[next_stage], next_stage, 0)
                    logger.info("CANDLE_%s_FAILED pair=%s volume_usd=%.2f", stage.upper(), pair["pair_address"], bar.volume_usd)
                    self.events.publish("candidate_updated")
                return
            self.db.set_candidate_state(pair_id, STATUS[stage], stage, self.settings.candidate_intervals[index])
            self.events.publish("candidate_updated")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors = pair["error_count"] + 1
            backoff = min(3 * (2 ** min(errors - 1, 4)), 60)
            self.db.candidate_error(pair_id, str(exc), backoff)
            self.last_error = f"Candle {stage}: {exc}"
            logger.warning("DEXSCREENER_REQUEST_FAILED pair=%s stage=%s error=%s", pair["pair_address"], stage, exc)
            self.events.publish("candidate_updated")

    async def market_loop(self):
        while True:
            try:
                due = self.db.due_market()
                if not due:
                    await asyncio.sleep(1)
                    continue
                for offset in range(0, len(due), 20):
                    group = due[offset : offset + 20]
                    ids = [pair["id"] for pair in group]
                    try:
                        markets = await self.api.pairs(self.settings.chain, [pair["pair_address"] for pair in group])
                        by_address = {item.get("pairAddress"): item for item in markets}
                        changed = False
                        for pair in group:
                            market = by_address.get(pair["pair_address"])
                            if market:
                                self.db.set_market(pair["id"], market,
                                                   self.settings.qualified_refresh if pair["status"] == "QUALIFIED" else 15)
                                changed = True
                            else:
                                self.db.defer_market([pair["id"]], 15)
                        if changed:
                            self.last_market_success = now_ms()
                            self.events.publish("qualified_updated")
                    except Exception as exc:
                        self.db.defer_market(ids, 15)
                        self.last_error = f"Market API: {exc}"
                        logger.warning("MARKET_DATA_ERROR error=%s", exc)
                        self.events.publish("health")
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MARKET_LOOP_ERROR")
                await asyncio.sleep(2)

    async def rvol_loop(self):
        while True:
            try:
                capacity = max(0, self.settings.chart_concurrency - len(self.rvol_inflight))
                if capacity:
                    for pair in self.db.due_rvol(limit=capacity + len(self.rvol_inflight)):
                        key = (pair["id"], pair["timeframe"])
                        if key in self.rvol_inflight:
                            continue
                        self.rvol_inflight.add(key)
                        task = asyncio.create_task(self._update_rvol(pair), name=f"rvol-{key[0]}-{key[1]}")
                        self.worker_tasks.add(task)
                        task.add_done_callback(lambda finished, item=key: self._finish_rvol(item, finished))
                        capacity -= 1
                        if capacity <= 0:
                            break
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RVOL_LOOP_ERROR")
                await asyncio.sleep(2)

    def _finish_rvol(self, key: tuple[int, str], task: asyncio.Task):
        self.rvol_inflight.discard(key)
        self.worker_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.error("RVOL_TASK_ERROR pair_id=%s timeframe=%s error=%s", *key, error)

    async def _update_rvol(self, pair: dict):
        timeframe = pair["timeframe"]
        index = STAGES.index(timeframe)
        try:
            bars = await self.browser.bars(pair, RESOLUTIONS[timeframe])
            ratio, baseline, current_bar = current_rvol(bars, RESOLUTIONS[timeframe], now_ms())
            chart = None
            if timeframe == "1m":
                chart = [{"time": bar.timestamp_ms, "open": bar.open_usd,
                          "high": bar.high_usd, "low": bar.low_usd, "close": bar.close_usd}
                         for bar in bars[-12:]]
            self.db.set_rvol(pair["id"], timeframe, ratio, baseline, current_bar,
                             self.settings.rvol_intervals[index], chart)
            self.last_chart_success = now_ms()
            if ratio != pair["previous_rvol"] or baseline != pair["previous_baseline"]:
                self.events.publish("qualified_updated")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.db.rvol_error(pair["id"], timeframe, str(exc), 30)
            self.last_error = f"RVOL {timeframe}: {exc}"
            logger.warning("RVOL_REQUEST_FAILED pair=%s timeframe=%s error=%s",
                           pair["pair_address"], timeframe, exc)
            if pair["previous_rvol"] is not None:
                self.events.publish("qualified_updated")

    def health(self):
        now = now_ms()
        counts = self.db.counts()
        live = bool(self.browser.socket_connected and self.browser.last_socket_frame_at
                    and now - self.browser.last_socket_frame_at <= 60_000)
        return {
            "status": "ok" if live else "starting",
            "discovery_status": "LIVE" if live else "STALE",
            "browser_connected": self.browser_connected,
            "discovery_connected": self.browser.socket_connected,
            "last_socket_frame_at": self.browser.last_socket_frame_at,
            "last_discovery_at": self.last_pair_seen,
            "candidate_count": counts["active_candidates"],
            "qualified_count": counts.get("QUALIFIED", 0),
            "discovery_source": "websocket" if self.browser.socket_pairs_seen else "dom_fallback_pending",
            "last_successful_scan": self.last_successful_scan,
            "last_pair_seen": self.last_pair_seen,
            "last_chart_success": self.last_chart_success,
            "last_market_success": self.last_market_success,
            "last_error": self.last_error,
            "discovery_rows": self.discovery_rows,
            "counts": counts,
            "started_at": self.started_at,
            "server_time": now,
        }
