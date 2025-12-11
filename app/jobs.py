from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import FastAPI

from .config import ApiConfig, AppConfig
from .process import PROCESSORS

logger = logging.getLogger(__name__)


class ApiJobState:
    def __init__(self) -> None:
        self.last_run: datetime | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.run_count: int = 0


class JobManager:
    def __init__(self, app: FastAPI, config: AppConfig) -> None:
        self._app = app
        self._config = config
        self._tasks: Dict[str, asyncio.Task[Any]] = {}
        self._state: Dict[str, ApiJobState] = {}

    @property
    def state(self) -> Dict[str, ApiJobState]:
        return self._state

    def start(self) -> None:
        """
        برای هر API فعال، یک job پس‌زمینه ایجاد می‌کند.
        """
        for api in self._config.apis:
            if not api.enabled:
                logger.info("API '%s' is disabled in config; skipping", api.name)
                continue
            if api.name in self._tasks:
                continue

            self._state[api.name] = ApiJobState()
            task = asyncio.create_task(self._run_loop(api), name=f"job-{api.name}")
            self._tasks[api.name] = task
            logger.info(
                "Started job for API '%s' with interval %s seconds",
                api.name,
                api.interval_seconds,
            )

    async def stop(self) -> None:
        """
        همه jobها را متوقف می‌کند (در shutdown اپ).
        """
        for task in self._tasks.values():
            task.cancel()
        for name, task in list(self._tasks.items()):
            try:
                await task
            except asyncio.CancelledError:
                logger.info("Job '%s' cancelled", name)
        self._tasks.clear()

    async def _run_loop(self, api_config: ApiConfig) -> None:
        """
        حلقه‌ی بی‌نهایت هر job:
        - fetch با retry
        - پردازش
        - ارسال به سرویس خارجی (اگر target_url تنظیم شده باشد)
        - صبر تا interval بعدی
        """
        client: httpx.AsyncClient = self._app.state.http_client
        processor = PROCESSORS.get(api_config.name)
        state = self._state[api_config.name]

        while True:
            state.last_run = datetime.now(timezone.utc)
            state.run_count += 1

            try:
                logger.debug("Running job '%s'", api_config.name)
                data = await self._fetch_with_retry(client, api_config)
                payload: Any = data
                if processor:
                    payload = processor(data)

                # ✔ در حالت تست: همیشه payload را در لاگ نمایش بده
                logger.info(
                    "Payload for API '%s' (background run): %s",
                    api_config.name,
                    payload,
                )

                # اگر بعداً target_url را تنظیم کنی، علاوه بر لاگ، ارسال هم انجام می‌شود
                if api_config.target_url:
                    await self._send_to_target(client, api_config, payload)

                state.last_success = datetime.now(timezone.utc)
                state.last_error = None
                logger.info("Job '%s' completed successfully", api_config.name)
            except Exception as exc:  # noqa: BLE001
                state.last_error = str(exc)
                logger.exception("Job '%s' failed: %s", api_config.name, exc)

            await asyncio.sleep(api_config.interval_seconds)

    async def _fetch_with_retry(
        self,
        client: httpx.AsyncClient,
        api_config: ApiConfig,
        extra_params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        صدا زدن API با سیاست retry مخصوص خودش.
        """
        last_error: Exception | None = None

        # params = query_params از config + هر چیزی که در لحظه پاس داده شود
        params: Dict[str, Any] | None = None
        if api_config.query_params:
            params = dict(api_config.query_params)
        if extra_params:
            params = params or {}
            params.update(extra_params)

        for attempt in range(1, api_config.max_retries + 1):
            try:
                response = await client.request(
                    method=api_config.method,
                    url=api_config.url,
                    params=params,
                    timeout=api_config.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Call to API '%s' failed on attempt %s/%s: %s",
                    api_config.name,
                    attempt,
                    api_config.max_retries,
                    exc,
                )
                if attempt < api_config.max_retries:
                    await asyncio.sleep(api_config.retry_backoff_seconds)

        assert last_error is not None
        raise last_error

    async def _send_to_target(
        self,
        client: httpx.AsyncClient,
        api_config: ApiConfig,
        payload: Any,
    ) -> None:
        """
        ارسال payload پردازش‌شده به سرویس خارجی.
        """
        response = await client.post(
            api_config.target_url,
            json=payload,
            timeout=api_config.timeout_seconds,
        )
        response.raise_for_status()
