from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import load_config
from .jobs import JobManager
from .models import ApiJobStatus
from .process import PROCESSORS


logger = logging.getLogger("findata")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    http_client = httpx.AsyncClient()
    job_manager = JobManager(app, config)

    app.state.config = config  # type: ignore[attr-defined]
    app.state.http_client = http_client  # type: ignore[attr-defined]
    app.state.job_manager = job_manager  # type: ignore[attr-defined]

    job_manager.start()
    logger.info("FinData startup completed")

    try:
        yield
    finally:
        await job_manager.stop()
        await http_client.aclose()
        logger.info("FinData shutdown completed")


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    fastapi_app = FastAPI(
        title="FinData",
        version="1.0.0",
        description="Simple and extensible financial data collector.",
        lifespan=lifespan,
    )

    @fastapi_app.get("/")
    async def read_root() -> Dict[str, str]:
        return {"message": "Service is up", "status": "ok"}

    @fastapi_app.get("/health", summary="Health check")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @fastapi_app.get(
        "/jobs",
        response_model=List[ApiJobStatus],
        summary="List background API jobs and their status",
    )
    async def list_jobs(request: Request) -> List[ApiJobStatus]:
        job_manager: JobManager = request.app.state.job_manager  # type: ignore[attr-defined]
        config = request.app.state.config  # type: ignore[attr-defined]
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

    @fastapi_app.post(
        "/jobs/{job_name}/run-once",
        summary="Trigger a single run of a job immediately",
    )
    async def run_job_once(job_name: str, request: Request) -> JSONResponse:
        job_manager: JobManager = request.app.state.job_manager  # type: ignore[attr-defined]
        config = request.app.state.config  # type: ignore[attr-defined]
        http_client: httpx.AsyncClient = request.app.state.http_client  # type: ignore[attr-defined]

        api_cfg = next((a for a in config.apis if a.name == job_name), None)
        if api_cfg is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"Job '{job_name}' not found"},
            )

        # تمام query string ها را برای این job می‌گیریم (مثل regno, insCode, showAll)
        extra_params: Dict[str, Any] = dict(request.query_params)
        if extra_params:
            logger.info(
                "Manual /run-once for job '%s' with query params: %s",
                job_name,
                extra_params,
            )

        try:
            data = await job_manager._fetch_with_retry(  # type: ignore[attr-defined]
                http_client,
                api_cfg,
                extra_params=extra_params or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Manual run of API '%s' failed: %s",
                api_cfg.name,
                exc,
            )
            return JSONResponse(
                status_code=502,
                content={"error": str(exc)},
            )

        payload: Any = data
        processor = PROCESSORS.get(api_cfg.name)
        if processor:
            payload = processor(data)

        logger.info(
            "Payload for job '%s' (manual /run-once): %s",
            job_name,
            payload,
        )

        if api_cfg.target_url:
            try:
                response = await http_client.post(
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

        state = job_manager.state.get(api_cfg.name)
        if state is not None:
            now = datetime.now(timezone.utc)
            state.last_run = now
            state.last_success = now
            state.last_error = None
            state.run_count += 1

        return JSONResponse(
            status_code=200,
            content={"status": "ok"},
        )

    @fastapi_app.post(
        "/jobs/run-all-once",
        summary="Run all configured jobs once and report HTTP 200 status",
    )
    async def run_all_jobs_once(request: Request) -> JSONResponse:
        job_manager: JobManager = request.app.state.job_manager  # type: ignore[attr-defined]
        config = request.app.state.config  # type: ignore[attr-defined]
        http_client: httpx.AsyncClient = request.app.state.http_client  # type: ignore[attr-defined]

        results: List[Dict[str, Any]] = []

        for api_cfg in config.apis:
            if not api_cfg.enabled:
                continue

            try:
                _ = await job_manager._fetch_with_retry(  # type: ignore[attr-defined]
                    http_client,
                    api_cfg,
                )
                status = "ok"
                error_message: str | None = None
            except Exception as exc:  # noqa: BLE001
                status = "error"
                error_message = str(exc)

            results.append(
                {
                    "name": api_cfg.name,
                    "url": api_cfg.url,
                    "status": status,
                    "error": error_message,
                },
            )

        return JSONResponse(
            status_code=200,
            content={"results": results},
        )

    return fastapi_app


app = create_app()
