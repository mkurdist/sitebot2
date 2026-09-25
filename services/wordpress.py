import aiohttp
import base64
from config import WC_URL, WP_USER, WP_APP_PASS

class WordPressService:
    _session = None

    def __init__(self):
        self.base_url = f"{WC_URL}/wp-json/wp/v2"
        credentials = f"{WP_USER}:{WP_APP_PASS}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }

    async def get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def create_post(self, data: dict):
        url = f"{self.base_url}/posts"
        session = await self.get_session()
        async with session.post(url, json=data) as response:
            if response.status not in [200, 201]:
                text = await response.text()
                raise Exception(f"WP API Error {response.status}: {text}")
            return await response.json()

    async def update_post(self, post_id: int, data: dict):
        url = f"{self.base_url}/posts/{post_id}"
        session = await self.get_session()
        async with session.post(url, json=data) as response:
            if response.status not in [200, 201]:
                text = await response.text()
                raise Exception(f"WP API Error {response.status}: {text}")
            return await response.json()

    async def upload_media(self, image_bytes: bytes, filename: str, alt_text: str, title: str):
        url = f"{self.base_url}/media"
        
        content_type = "image/jpeg"
        if filename.lower().endswith(".webp"):
            content_type = "image/webp"
        elif filename.lower().endswith(".png"):
            content_type = "image/png"
            
        headers = {
            "Authorization": self.headers["Authorization"],
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type
        }
        
        session = await self.get_session()
        async with session.post(url, data=image_bytes, headers=headers) as response:
            if response.status not in [200, 201]:
                text = await response.text()
                raise Exception(f"WP Media API Error {response.status}: {text}")
            
            media_data = await response.json()
            media_id = media_data['id']
            
            update_url = f"{self.base_url}/media/{media_id}"
            update_payload = {
                "alt_text": alt_text,
                "title": title
            }
            async with session.post(update_url, json=update_payload, headers=self.headers):
                pass 
                
            return media_id

    async def get_recent_posts(self, per_page=10):
        url = f"{self.base_url}/posts"
        params = {"per_page": per_page, "orderby": "date", "order": "desc", "status": "any", "context": "edit"}
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WP API Error {response.status}: {text}")
            return await response.json()

    async def get_post(self, post_id: int):
        url = f"{self.base_url}/posts/{post_id}"
        session = await self.get_session()
        async with session.get(url, params={"context": "edit"}) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WP API Error {response.status}: {text}")
            return await response.json()

    async def get_categories(self):
        url = f"{self.base_url}/categories"
        params = {"per_page": 100, "hide_empty": "0"}
        session = await self.get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"WP API Error {response.status}: {text}")
            return await response.json()

wp_service_instance = WordPressService()
