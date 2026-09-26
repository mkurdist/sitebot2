"""
🌟 ماژول ایزوله تست سلامت سیستم.
سه سرویس (ووکامرس، وردپرس، دیتابیس) را موازی پینگ می‌کند و هرگز خودش
Exception پرتاب نمی‌کند؛ همیشه یک دیکشنری نتیجه برمی‌گرداند.
"""

import asyncio
import time

from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service
from services.database import db_service


async def _check_db() -> dict:
    t0 = time.monotonic()
    try:
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        ms = int((time.monotonic() - t0) * 1000)
        size = pool.get_size()
        idle = pool.get_idle_size()
        return {"ok": True, "detail": f"{ms}ms | {size - idle}/{size} کانکشن فعال"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:150]}


async def _check_wc() -> dict:
    t0 = time.monotonic()
    try:
        await wc_service.get_categories()
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "detail": f"{ms}ms"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:150]}


async def _check_wp() -> dict:
    t0 = time.monotonic()
    try:
        await wp_service.get_categories()
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "detail": f"{ms}ms"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:150]}


async def run_health_check() -> dict:
    db_r, wc_r, wp_r = await asyncio.gather(_check_db(), _check_wc(), _check_wp())
    return {"db": db_r, "wc": wc_r, "wp": wp_r}
