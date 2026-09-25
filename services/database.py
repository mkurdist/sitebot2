import asyncpg
from config import DATABASE_URL

class DatabaseService:
    _pool = None

    async def get_pool(self):
        if self._pool is None:
            self._pool = await asyncpg.create_pool(DATABASE_URL)
            await self._init_tables()
        return self._pool

    async def _init_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS order_notifications (
                    order_id VARCHAR(50) PRIMARY KEY,
                    status VARCHAR(50),
                    message_id BIGINT
                )
            ''')

    async def close(self):
        if self._pool:
            await self._pool.close()

# ایجاد یک نمونه سراسری (Singleton)
db_service = DatabaseService()
