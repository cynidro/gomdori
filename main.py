import asyncio
import calendar
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

import scraper

app = FastAPI(title="gomdori-schedule")

_cache: dict[str, dict] = {}
CACHE_TTL = 3600

STATIC_DIR = Path(__file__).parent / "static"


# ── 정적 파일 ────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.json")


# ── API ──────────────────────────────────────────────────────────────────────
@app.get("/api/schedule")
def get_schedule(year: int | None = None, month: int | None = None):
    now = datetime.now()
    if year is None: year = now.year
    if month is None: month = now.month
    if not (1 <= month <= 12) or not (2020 <= year <= 2099):
        raise HTTPException(status_code=400, detail="Invalid year/month")

    cache_key = f"{year}-{month:02d}"
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached["fetched_at"]) < CACHE_TTL:
        return JSONResponse({"year": year, "month": month, "schedule": cached["data"], "cached": True})

    try:
        data = scraper.get_schedule(year, month)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"스크래핑 실패: {e}")

    _cache[cache_key] = {"data": data, "fetched_at": time.time()}
    return JSONResponse({"year": year, "month": month, "schedule": data, "cached": False})


@app.post("/api/refresh")
def refresh_schedule(year: int, month: int):
    """캐시를 무효화하고 강제로 다시 스크래핑한다."""
    if not (1 <= month <= 12) or not (2020 <= year <= 2099):
        raise HTTPException(status_code=400, detail="Invalid year/month")

    cache_key = f"{year}-{month:02d}"
    _cache.pop(cache_key, None)

    try:
        data = scraper.get_schedule(year, month)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"스크래핑 실패: {e}")

    _cache[cache_key] = {"data": data, "fetched_at": time.time()}
    total_count = sum(len(v) for v in data.values())
    return JSONResponse({
        "year": year,
        "month": month,
        "schedule": data,
        "count": total_count,
        "found": total_count > 0,
    })


# ── 월말 자동 선제 캐싱 ───────────────────────────────────────────────────────
async def _prefetch(year: int, month: int):
    key = f"{year}-{month:02d}"
    try:
        data = scraper.get_schedule(year, month)
        _cache[key] = {"data": data, "fetched_at": time.time()}
        print(f"[prefetch] {year}-{month:02d} → {len(data)}일 캐시 완료", flush=True)
    except Exception as e:
        print(f"[prefetch] {year}-{month:02d} 실패: {e}", flush=True)


async def _scheduler():
    """30분마다 확인 — 말일 19시면 다음 달 일정을 선제 캐싱."""
    while True:
        await asyncio.sleep(1800)
        now = datetime.now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day == last_day and now.hour == 19:
            nm = now.month % 12 + 1
            ny = now.year + (1 if now.month == 12 else 0)
            key = f"{ny}-{nm:02d}"
            cached = _cache.get(key)
            if not cached or (time.time() - cached["fetched_at"]) > CACHE_TTL:
                await _prefetch(ny, nm)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_scheduler())
