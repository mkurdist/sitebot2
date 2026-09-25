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
                command_timeout=60,
                max_inactive_connection_lifetime=300
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
