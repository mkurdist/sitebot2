"""
🌟 ماژول کاملاً ایزوله «تنظیمات پیشرفته»
سرویس مرکزی key-value برای تمام تنظیمات پنل + مدیریت ادمین‌های کمکی + لاگ خطا.

- جدول‌های خودش را خودش می‌سازد (bot_settings / bot_admins / bot_error_log)
- از همان pool مشترک دیتابیس (db_service) استفاده می‌کند، بدون دست‌زدن به database.py
- یک کش درون‌حافظه‌ای دارد تا خواندن تنظیمات صفر لتنسی باشد؛
  نوشتن هر بار هم دیتابیس و هم کش را آپدیت می‌کند -> با ری‌استارت هم از بین نمی‌رود.
"""

import asyncio
from services.database import db_service

_cache: dict = {}
_admins_cache: set = set()
_lock = asyncio.Lock()

# مقدار پیش‌فرض هر کلید: (نوع, مقدار خام)
DEFAULTS = {
    "notif_enabled_pending": ("bool", "true"),
    "notif_enabled_processing": ("bool", "true"),
    "notif_enabled_completed": ("bool", "true"),
    "notif_enabled_cancelled": ("bool", "true"),
    "notif_enabled_on_hold": ("bool", "true"),
    "notif_enabled_failed": ("bool", "true"),

    "channel_id": ("str", ""),
    "channel_mode": ("str", "both"),          # both | channel_only
    "channel_scope": ("str", "all_status"),   # all_status | new_only

    "sync_auto_enabled": ("bool", "false"),
    "sync_auto_hour": ("int", "3"),
    "sync_last_products_at": ("str", ""),
    "sync_last_products_count": ("int", "0"),
    "sync_last_articles_at": ("str", ""),
    "sync_last_articles_count": ("int", "0"),
}

MAX_ERROR_LOG = 50


class SettingsService:
    def __init__(self):
        self._ready = False

    # ------------------------------------------------------------------
    # راه‌اندازی
    # ------------------------------------------------------------------
    async def _ensure_tables(self):
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    value_type VARCHAR(10) DEFAULT 'str',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_admins (
                    user_id BIGINT PRIMARY KEY,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_error_log (
                    id SERIAL PRIMARY KEY,
                    source TEXT,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    async def _load_cache(self):
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value, value_type FROM bot_settings")
            for r in rows:
                _cache[r["key"]] = (r["value_type"], r["value"])
            admin_rows = await conn.fetch("SELECT user_id FROM bot_admins")
            _admins_cache.clear()
            _admins_cache.update(r["user_id"] for r in admin_rows)

    async def ensure_ready(self):
        if self._ready:
            return
        async with _lock:
            if self._ready:
                return
            await self._ensure_tables()
            await self._load_cache()
            self._ready = True

    # ------------------------------------------------------------------
    # خواندن/نوشتن تنظیمات
    # ------------------------------------------------------------------
    def _cast(self, vtype: str, raw: str):
        if vtype == "bool":
            return raw == "true"
        if vtype == "int":
            try:
                return int(raw)
            except Exception:
                return 0
        return raw

    async def get(self, key: str):
        await self.ensure_ready()
        if key in _cache:
            vtype, raw = _cache[key]
            return self._cast(vtype, raw)
        if key in DEFAULTS:
            vtype, raw = DEFAULTS[key]
            return self._cast(vtype, raw)
        return None

    async def set(self, key: str, value):
        await self.ensure_ready()
        vtype = DEFAULTS.get(key, ("str", ""))[0]
        raw = "true" if value else "false" if vtype == "bool" else str(value)
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO bot_settings (key, value, value_type)
                VALUES ($1, $2, $3)
                ON CONFLICT (key) DO UPDATE
                SET value = $2, value_type = $3, updated_at = CURRENT_TIMESTAMP
            ''', key, raw, vtype)
        _cache[key] = (vtype, raw)

    async def toggle(self, key: str) -> bool:
        current = await self.get(key)
        new_val = not bool(current)
        await self.set(key, new_val)
        return new_val

    # ------------------------------------------------------------------
    # ادمین‌های کمکی
    # ------------------------------------------------------------------
    async def list_extra_admins(self) -> set:
        await self.ensure_ready()
        return set(_admins_cache)

    async def add_admin(self, user_id: int, added_by: int):
        await self.ensure_ready()
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO bot_admins (user_id, added_by) VALUES ($1, $2)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id, added_by)
        _admins_cache.add(user_id)

    async def remove_admin(self, user_id: int):
        await self.ensure_ready()
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM bot_admins WHERE user_id = $1", user_id)
        _admins_cache.discard(user_id)

    # ------------------------------------------------------------------
    # لاگ خطا (فقط خطاهایی که خود ماژول تنظیمات تشخیص می‌دهد: تست سلامت،
    # سینک خودکار، اتصال کانال و ...). هرگز خودش نباید کرش کند.
    # ------------------------------------------------------------------
    async def log_error(self, source: str, message: str):
        try:
            await self.ensure_ready()
            pool = await db_service.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO bot_error_log (source, message) VALUES ($1, $2)",
                    source, str(message)[:500]
                )
                await conn.execute('''
                    DELETE FROM bot_error_log WHERE id NOT IN (
                        SELECT id FROM bot_error_log ORDER BY created_at DESC LIMIT $1
                    )
                ''', MAX_ERROR_LOG)
        except Exception:
            pass

    async def get_recent_errors(self, limit: int = 10):
        await self.ensure_ready()
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                "SELECT source, message, created_at FROM bot_error_log ORDER BY created_at DESC LIMIT $1",
                limit
            )


settings_service = SettingsService()
