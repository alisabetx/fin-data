from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import load_config
from .jobs import JobManager
from .models import ApiJobStatus
from .process import PROCESSORS


logger = logging.getLogger("findata")


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    app = FastAPI(
        title="FinData",
        version="1.0.0",
        description="Simple and extensible financial data collector.",
    )

    @app.on_event("startup")
    async def on_startup() -> None:
        config = load_config()
        app.state.config = config
        app.state.http_client = httpx.AsyncClient()
        app.state.job_manager = JobManager(app, config)
        app.state.job_manager.start()
        logger.info("FinData startup completed")

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        job_manager: JobManager = app.state.job_manager
        await job_manager.stop()
        client: httpx.AsyncClient = app.state.http_client
        await client.aclose()
        logger.info("FinData shutdown completed")

    @app.get("/")
    def read_root():
        return {"message": "Service is up", "status": "ok"}

    @app.get("/health", summary="Health check")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/jobs",
        response_model=List[ApiJobStatus],
        summary="List background API jobs and their status",
    )
    async def list_jobs() -> List[ApiJobStatus]:
        job_manager: JobManager = app.state.job_manager
        config = app.state.config
        statuses: List[ApiJobStatus] = []

        for api_cfg in config.apis:
            state = job_manager.state.get(api_cfg.name)
            if state is None:
                continue
            statuses.append(
                ApiJobStatus(
                    name=api_cfg.name,
                    url=api_cfg.url,
                    interval_seconds=api_cfg.interval_seconds,
                    last_run=state.last_run,
                    last_success=state.last_success,
                    last_error=state.last_error,
                    run_count=state.run_count,
                    enabled=api_cfg.enabled,
                ),
            )
        return statuses

    @app.post(
        "/jobs/{job_name}/run-once",
        summary="Trigger a single run of a job immediately",
    )
    async def run_job_once(job_name: str) -> JSONResponse:
        job_manager: JobManager = app.state.job_manager
        config = app.state.config
        api_cfg = next((a for a in config.apis if a.name == job_name), None)
        if api_cfg is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"Job '{job_name}' not found"},
            )

        client: httpx.AsyncClient = app.state.http_client

        last_error: Exception | None = None
        data: Any | None = None
        for attempt in range(1, api_cfg.max_retries + 1):
            try:
                response = await client.request(
                    method=api_cfg.method,
                    url=api_cfg.url,
                    timeout=api_cfg.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Manual run of API '%s' failed on attempt %s/%s: %s",
                    api_cfg.name,
                    attempt,
                    api_cfg.max_retries,
                    exc,
                )
                if attempt < api_cfg.max_retries:
                    await asyncio.sleep(api_cfg.retry_backoff_seconds)

        if data is None:
            assert last_error is not None
            return JSONResponse(
                status_code=502,
                content={"error": str(last_error)},
            )

        payload: Any = data
        processor = PROCESSORS.get(api_cfg.name)
        if processor:
            payload = processor(data)

        # ✔ در حالت تست: payload اجرای دستی را در لاگ نمایش بده
        logger.info(
            "Payload for job '%s' (manual /run-once): %s",
            job_name,
            payload,
        )

        if api_cfg.target_url:
            try:
                response = await client.post(
                    api_cfg.target_url,
                    json=payload,
                    timeout=api_cfg.timeout_seconds,
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse(
                    status_code=502,
                    content={"error": f"Error sending to target: {exc}"},
                )

        # Update job state for visibility
        state = job_manager.state.get(api_cfg.name)
        if state is not None:
            from datetime import datetime

            now = datetime.utcnow()
            state.last_run = now
            state.last_success = now
            state.last_error = None
            state.run_count += 1

        return JSONResponse(
            status_code=200,
            content={"status": "ok"},
        )

    return app


app = create_app()
