import aiohttp
from config import WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET

class WooCommerceService:
    _session = None 

    def __init__(self):
        self.base_url = WC_URL
        self.auth = aiohttp.BasicAuth(WC_CONSUMER_KEY, WC_CONSUMER_SECRET)

    async def get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(auth=self.auth)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # 🌟 متد جدید برای دریافت محصولات با صفحه‌بندی (مخصوص همگام‌سازی دیتابیس)
    async def get_products(self, page=1, per_page=20):
        url = f"{self.base_url}/wp-json/wc/v3/products"
        params = {"per_page": per_page, "page": page}
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def get_latest_products(self, per_page=3):
        url = f"{self.base_url}/wp-json/wc/v3/products"
        params = {"per_page": per_page, "orderby": "date", "order": "desc"}
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def create_simple_product(self, data: dict):
        url = f"{self.base_url}/wp-json/wc/v3/products"
        session = await self.get_session()
        async with session.post(url, json=data) as response:
            if response.status not in [200, 201]:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def get_categories(self):
        url = f"{self.base_url}/wp-json/wc/v3/products/categories"
        params = {"per_page": 50, "hide_empty": "0"} 
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def get_product(self, product_id: int):
        url = f"{self.base_url}/wp-json/wc/v3/products/{product_id}"
        session = await self.get_session()
        async with session.get(url) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def update_product(self, product_id: int, data: dict):
        url = f"{self.base_url}/wp-json/wc/v3/products/{product_id}"
        session = await self.get_session()
        async with session.put(url, json=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def delete_product(self, product_id: int):
        url = f"{self.base_url}/wp-json/wc/v3/products/{product_id}"
        session = await self.get_session()
        async with session.delete(url) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def get_recent_orders(self, per_page=10):
        url = f"{self.base_url}/wp-json/wc/v3/orders"
        params = {"per_page": per_page, "orderby": "date", "order": "desc"}
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def get_order(self, order_id: int):
        url = f"{self.base_url}/wp-json/wc/v3/orders/{order_id}"
        session = await self.get_session()
        async with session.get(url) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

    async def update_order_status(self, order_id: int, status: str):
        url = f"{self.base_url}/wp-json/wc/v3/orders/{order_id}"
        payload = {"status": status}
        session = await self.get_session()
        async with session.put(url, json=payload) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WC API Error {response.status}: {text}")
            return await response.json()

wc_service_instance = WooCommerceService()
