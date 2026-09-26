"""
🌟 ماژول ایزوله زمان‌بندی خودکار Sync.

عمداً منطق سینک را (سبک و بی‌صدا، بدون message.answer) اینجا تکرار می‌کند
به‌جای فراخوانی مستقیم هندلرهای admin_sync.py، چون آن هندلرها به آبجکت
Message وابسته‌اند و نباید برای اجرای خودکار دست‌کاری شوند.

نحوه اجرا: هر ۵ دقیقه چک می‌شود؛ اگر «فعال» باشد و ساعت فعلی == ساعت
تنظیم‌شده باشد و امروز قبلاً اجرا نشده باشد، یک‌بار سینک کامل انجام می‌شود.
"""

import asyncio
import datetime

from services.settings_service import settings_service
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service
from services.database import db_service

CHECK_INTERVAL = 300  # ثانیه
_last_run_date = None  # جلوگیری از اجرای دوباره در همان روز (State در RAM کافی است)


async def _sync_products_silent() -> int:
    page, per_page, total = 1, 20, 0
    pool = await db_service.get_pool()
    while True:
        products = await wc_service.get_products(page=page, per_page=per_page)
        if not products:
            break
        async with pool.acquire() as conn:
            for p in products:
                images = p.get("images", [])
                p_image = images[0]["src"] if images else ""
                await conn.execute('''
                    INSERT INTO products (product_id, name, slug, permalink, image_url, mention_count)
                    VALUES ($1, $2, $3, $4, $5, 0)
                    ON CONFLICT (product_id) DO UPDATE
                    SET name = $2, slug = $3, permalink = $4, image_url = $5
                ''', p["id"], p["name"], p["slug"], p["permalink"], p_image)
                total += 1
        await asyncio.sleep(2)
        page += 1
    return total


async def _sync_articles_silent() -> int:
    page, per_page, total = 1, 20, 0
    pool = await db_service.get_pool()
    session = await wp_service.get_session()
    while True:
        url = f"{wp_service.base_url}/posts"
        params = {"per_page": per_page, "page": page, "status": "publish"}
        async with session.get(url, params=params) as response:
            if response.status in (400, 404):
                break
            if response.status != 200:
                raise Exception(await response.text())
            posts = await response.json()
            if not posts:
                break
            async with pool.acquire() as conn:
                for post in posts:
                    title = post.get("title", {}).get("rendered", "بدون عنوان")
                    slug = post.get("slug", "")
                    meta = post.get("meta", {})
                    focus_keyword = meta.get("rank_math_focus_keyword") or slug
                    tags_str = ",".join(map(str, post.get("tags", [])))
                    try:
                        await conn.execute('''
                            INSERT INTO seo_ledger (focus_keyword, slug, title, tags)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (focus_keyword) DO NOTHING
                        ''', focus_keyword, slug, title, tags_str)
                        total += 1
                    except Exception:
                        pass
        await asyncio.sleep(2)
        page += 1
    return total


async def _run_once():
    now = datetime.datetime.now()
    try:
        n = await _sync_products_silent()
        await settings_service.set("sync_last_products_at", now.strftime("%Y-%m-%d %H:%M"))
        await settings_service.set("sync_last_products_count", n)
    except Exception as e:
        await settings_service.log_error("auto_sync_products", str(e))

    try:
        n = await _sync_articles_silent()
        await settings_service.set("sync_last_articles_at", now.strftime("%Y-%m-%d %H:%M"))
        await settings_service.set("sync_last_articles_count", n)
    except Exception as e:
        await settings_service.log_error("auto_sync_articles", str(e))


async def scheduler_loop():
    """این تابع باید فقط یک‌بار، در main() ربات، با asyncio.create_task اجرا شود."""
    global _last_run_date
    await asyncio.sleep(15)  # اجازه بده دیتابیس/سرویس‌ها کامل بالا بیایند
    while True:
        try:
            enabled = await settings_service.get("sync_auto_enabled")
            hour = await settings_service.get("sync_auto_hour")
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if enabled and now.hour == int(hour) and _last_run_date != today_str:
                _last_run_date = today_str
                await _run_once()
        except Exception as e:
            await settings_service.log_error("scheduler_loop", str(e))
        await asyncio.sleep(CHECK_INTERVAL)
