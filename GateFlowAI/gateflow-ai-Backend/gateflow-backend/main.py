"""
main.py — GateFlow AI entry point

Start: uvicorn main:app --reload
Docs:  http://localhost:8000/docs
"""
import os
from contextlib import asynccontextmanager
from urllib.parse import unquote
from uuid import UUID

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from database import create_tables, ensure_pg_userrole_enum_values, ensure_pg_invite_enum_values, engine
from utils.logger import configure_logging, logger
from utils.rate_limiter import _rate_limit_exceeded_handler, limiter
from utils.redis_client import ping_redis


# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(f"[START] {settings.APP_NAME} v{settings.APP_VERSION}")
    try:
        await create_tables()
    except Exception as e:
        logger.error(f"[FAIL] DB setup failed: {e}")
    try:
        await ensure_pg_userrole_enum_values()
    except Exception as e:
        logger.warning(f"[WARN] PG role enum extension skipped: {e}")
    try:
        await ensure_pg_invite_enum_values()
    except Exception as e:
        logger.warning(f"[WARN] PG invite enum extension skipped: {e}")
    await ping_redis()

    # Start overstay detection — interval from settings (default 1 min for timely alerts)
    from services.overstay_service import check_overstays
    poll_m = max(1, int(settings.OVERSTAY_POLL_INTERVAL_MINUTES))
    scheduler.add_job(check_overstays, "interval", minutes=poll_m, id="overstay_check")
    scheduler.start()
    logger.info(f"[OK] Overstay scheduler started (every {poll_m} min)")
    logger.info("[OK] Ready")

    yield

    scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("[STOP] Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Smart Event Access & Guest Intelligence Platform",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    redirect_slashes=False,
    lifespan=lifespan,
)

# Middleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — explicit origin list from .env + a regex that covers any localhost/127.0.0.1
# port in development so a mismatched port never causes "No Access-Control-Allow-Origin".
# In production, set APP_ENV=production and ALLOWED_ORIGINS to your real domain(s).
_cors_origins = settings.allowed_origins_list
_cors_kwargs: dict = dict(
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if settings.is_development:
    # Covers http://localhost:<any-port> and http://127.0.0.1:<any-port>
    _cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"
app.add_middleware(CORSMiddleware, **_cors_kwargs)

# Unhandled-exception handler — ensures CORS headers are present even on 500s.
# Without this, a backend crash returns a bare 500 with no Access-Control-Allow-Origin
# header, which the browser misreports as a CORS error instead of a server error.
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse
import traceback as _traceback

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: _Request, exc: Exception) -> _JSONResponse:
    origin = request.headers.get("origin", "")
    logger.error(f"[ERROR] Unhandled exception on {request.method} {request.url.path}: {exc}\n{_traceback.format_exc()}")
    headers = {}
    # Reflect the request origin so the browser can read the error body
    allowed = set(settings.allowed_origins_list)
    import re as _re
    _localhost_re = _re.compile(r"https?://(localhost|127\.0\.0\.1)(:\d+)?$")
    if origin and (origin in allowed or (settings.is_development and _localhost_re.match(origin))):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return _JSONResponse(
        status_code=500,
        content={"detail": "Internal server error — check backend logs for traceback"},
        headers=headers,
    )

# ── REST Routes ────────────────────────────────────────────────────────────────
from routes.auth_routes         import router as auth_router
from routes.space_routes        import router as space_router
from routes.invite_routes       import router as invite_router
from routes.visitor_routes      import router as visitor_router
from routes.entry_routes        import router as entry_router
from routes.exit_routes         import router as exit_router
from routes.walkin_routes       import router as walkin_router
from routes.dashboard_routes    import router as dashboard_router
from routes.overstay_routes     import router as overstay_router
from routes.notification_routes import router as notification_router
from routes.document_routes     import router as document_router
from routes.chat_routes         import router as chat_router  # secure visitor chat proxy

app.include_router(auth_router,         prefix="/auth",          tags=["Auth"])
app.include_router(space_router,        prefix="/spaces",        tags=["Spaces"])
app.include_router(invite_router,       prefix="/invites",       tags=["Invites"])
app.include_router(visitor_router,      prefix="/visitor",       tags=["Visitor"])
app.include_router(entry_router,        prefix="/entry",         tags=["Entry"])
app.include_router(exit_router,         prefix="/exit",          tags=["Exit"])
app.include_router(walkin_router,       prefix="/walkins",       tags=["Walk-In"])
app.include_router(dashboard_router,    prefix="/dashboard",     tags=["Dashboard"])
app.include_router(overstay_router,     prefix="/overstay",      tags=["Overstay"])
app.include_router(notification_router, prefix="/notifications", tags=["Notifications"])
app.include_router(document_router,     prefix="/documents",     tags=["Documents"])
app.include_router(chat_router,         prefix="/chat",          tags=["Chat"])


# ── WebSocket ──────────────────────────────────────────────────────────────────
from websocket.dashboard_ws import manager

@app.websocket("/ws/dashboard/{space_id}")
async def dashboard_ws(websocket: WebSocket, space_id: str):
    """
    Live dashboard WebSocket (authenticated).

    Connect with:
      ws://host/ws/dashboard/<space_id>?token=<access_jwt>

    ORGANIZER / RESIDENT: only spaces they own. ADMIN: any space.
    """
    raw = websocket.query_params.get("token") or websocket.query_params.get("access_token")
    if not raw:
        await websocket.close(code=1008)
        return
    token = unquote(raw).strip()
    if not token:
        await websocket.close(code=1008)
        return

    try:
        sid = UUID(space_id)
    except ValueError:
        await websocket.close(code=1008)
        return

    from database import AsyncSessionLocal
    from dependencies import user_from_access_token
    from services.space_service import ensure_space_access

    async with AsyncSessionLocal() as db:
        try:
            user = await user_from_access_token(db, token)
            await ensure_space_access(db, sid, user)
        except HTTPException:
            await websocket.close(code=1008)
            return

    sid_str = str(sid)
    await manager.connect(sid_str, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(sid_str, websocket)


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return JSONResponse({"app": settings.APP_NAME, "version": settings.APP_VERSION, "docs": "/docs"})


@app.get("/health", tags=["Health"])
async def health():
    return JSONResponse({"status": "healthy"})
