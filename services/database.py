import asyncpg
from config import DATABASE_URL

class DatabaseService:
    _pool = None

    async def get_pool(self):
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=3,
                command_timeout=60,          # سقف زمانی هر کوئری روی کانکشن‌های موجود
                timeout=10,                  # 🌟 سقف زمانی برقراری کانکشن جدید (قبلاً تنظیم نشده بود -> پیش‌فرض ۶۰ ثانیه بود)
                max_inactive_connection_lifetime=180  # 🌟 کانکشن‌های بی‌کار زودتر بازیافت می‌شوند تا ریسک کانکشن مرده کم شود
            )
            await self._init_tables()
        return self._pool

    async def _init_tables(self):
        async with self._pool.acquire() as conn:
            # ۱. جدول سفارشات (موجود از قبل)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS order_notifications (
                    order_id VARCHAR(50) PRIMARY KEY,
                    status VARCHAR(50),
                    message_id BIGINT
                )
            ''')

            # 🌟 مهاجرت دیتابیس: ستون‌های جدید برای ماشین‌حالت اعلان سفارش
            # payment_state: گروه منطقی وضعیت (pending/paid/cancelled/failed) برای تشخیص تغییر معنادار
            # admin_confirmed: ماندگاری تایید ادمین در دیتابیس (در برابر ری‌استارت ربات)
            await conn.execute('''
                ALTER TABLE order_notifications
                ADD COLUMN IF NOT EXISTS payment_state VARCHAR(20)
            ''')
            await conn.execute('''
                ALTER TABLE order_notifications
                ADD COLUMN IF NOT EXISTS admin_confirmed BOOLEAN DEFAULT FALSE
            ''')
            
            # ۲. 🌟 جدول جدید کاتالوگ محصولات (برای توزیع عادلانه لینک)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    product_id BIGINT PRIMARY KEY,
                    name TEXT,
                    slug TEXT,
                    permalink TEXT,
                    image_url TEXT,
                    mention_count INTEGER DEFAULT 0
                )
            ''')
            
            # ۳. 🌟 جدول جدید بایگانی سئو (جلوگیری از همنوع‌خواری کلمات کلیدی)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS seo_ledger (
                    id SERIAL PRIMARY KEY,
                    focus_keyword TEXT UNIQUE,
                    slug TEXT,
                    title TEXT,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    async def close(self):
        if self._pool:
            await self._pool.close()

# ایجاد یک نمونه سراسری (Singleton)
db_service = DatabaseService()
