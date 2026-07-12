import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from typing import Literal, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Response, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, HttpUrl

from .config import Settings
from .pool import (
    BrowserServerPool,
    BrowserServerUnavailable,
    Lease,
    LeaseNotFound,
    PoolBusy,
)
from .profiles import Platform, resolve_profile


class LeaseRequest(BaseModel):
    url: Optional[HttpUrl] = Field(
        default=None,
        description="用于自动选择平台 UA 的目标地址；控制面不会访问该地址",
    )
    platform: Platform = Field(
        default=Platform.AUTO,
        description="显式平台优先于 URL 自动识别",
    )


class LeaseResult(BaseModel):
    lease_id: str
    ws_endpoint: str
    browser_type: Literal["firefox"] = "firefox"
    playwright_version: str
    camoufox_version: str
    browser_version: str
    platform: Platform
    user_agent: Optional[str]
    expires_at: datetime


class PoolStats(BaseModel):
    started: bool
    instance_id: str
    servers: int
    connected_servers: int
    warm_servers: int
    leased: int
    available: int
    starting_servers: int
    warm_pool_size: int
    base_port: int
    restarts: int


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    pool = BrowserServerPool(settings)
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
    playwright_version = version("playwright")
    camoufox_version = version("camoufox")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await pool.start()
        try:
            yield
        finally:
            await pool.stop()

    app = FastAPI(
        title="CamoPool BrowserServer API",
        version=version("camopool"),
        description=(
            "Camoufox Playwright BrowserServer 租约控制面。"
            "申请租约后使用 playwright.firefox.connect(ws_endpoint) 直接操作浏览器。"
        ),
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "存活与就绪探针"},
            {"name": "leases", "description": "申请、续期、查询和释放 BrowserServer"},
            {"name": "pool", "description": "BrowserServer 池状态"},
        ],
    )

    @app.middleware("http")
    async def instance_header(request, call_next):
        response = await call_next(request)
        response.headers["X-CamoPool-Instance"] = settings.instance_id
        return response

    async def authenticate(x_api_key: Optional[str] = Security(api_key_header)) -> None:
        if settings.api_key and (
            not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key)
        ):
            raise HTTPException(status_code=401, detail="invalid API key")

    def result(lease: Lease) -> LeaseResult:
        return LeaseResult(
            lease_id=lease.lease_id,
            ws_endpoint=lease.ws_endpoint,
            playwright_version=playwright_version,
            camoufox_version=camoufox_version,
            browser_version=lease.browser_version,
            platform=lease.platform,
            user_agent=lease.user_agent,
            expires_at=lease.expires_at,
        )

    @app.get("/health/live", tags=["health"], summary="存活检查")
    async def live():
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        summary="BrowserServer 池就绪检查",
        response_model=PoolStats,
    )
    async def ready(response: Response):
        stats = pool.stats()
        if not stats["started"]:
            response.status_code = 503
        return stats

    @app.get(
        "/v1/pool/stats",
        tags=["pool"],
        summary="读取 BrowserServer 池状态",
        response_model=PoolStats,
        dependencies=[Depends(authenticate)],
    )
    async def stats():
        return pool.stats()

    @app.post(
        "/v1/leases",
        tags=["leases"],
        summary="申请 BrowserServer 租约",
        response_model=LeaseResult,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(authenticate)],
    )
    async def acquire(payload: LeaseRequest):
        profile = resolve_profile(str(payload.url or ""), payload.platform)
        try:
            return result(await pool.acquire(profile))
        except PoolBusy as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
                headers={"Retry-After": "1"},
            ) from exc
        except BrowserServerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(
        "/v1/leases/{lease_id}",
        tags=["leases"],
        summary="查询租约",
        response_model=LeaseResult,
        dependencies=[Depends(authenticate)],
    )
    async def get_lease(lease_id: str):
        try:
            return result(pool.get(lease_id))
        except LeaseNotFound as exc:
            raise HTTPException(status_code=404, detail="lease not found") from exc

    @app.post(
        "/v1/leases/{lease_id}/renew",
        tags=["leases"],
        summary="续期租约",
        response_model=LeaseResult,
        dependencies=[Depends(authenticate)],
    )
    async def renew(lease_id: str):
        try:
            return result(pool.renew(lease_id))
        except LeaseNotFound as exc:
            raise HTTPException(status_code=404, detail="lease not found") from exc

    @app.delete(
        "/v1/leases/{lease_id}",
        tags=["leases"],
        summary="释放并重置 BrowserServer",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(authenticate)],
    )
    async def release(lease_id: str):
        try:
            await pool.release(lease_id)
        except LeaseNotFound as exc:
            raise HTTPException(status_code=404, detail="lease not found") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()


def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=os.getenv("CAMOPOOL_LOG_LEVEL", "INFO"))
    uvicorn.run("camopool.api:app", host=settings.host, port=settings.port, workers=1)
