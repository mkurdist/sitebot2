import os
import json
import aiohttp
import asyncio
from dotenv import load_dotenv

load_dotenv()

def _api_key(): return os.getenv("GEMINI_API_KEY", "").strip()
def _model(): return os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
def _base(): return os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

class GeminiBlogError(Exception):
    pass

# ==========================================
# ۱. تولید عناوین جذاب (ایده‌پردازی)
# ==========================================
async def generate_blog_titles(topic: str) -> list:
    url = f"{_base()}/models/{_model()}:generateContent"
    headers = {"x-goog-api-key": _api_key(), "Content-Type": "application/json"}
    
    prompt = (
        f"تو یک متخصص سئو و کپی‌رایتر سایت 'شهر سفال' هستی. "
        f"برای موضوع '{topic}' دقیقاً ۵ عنوان مقاله بسیار جذاب، کلیک‌خور و سئوشده (بین ۵۰ تا ۶۵ کاراکتر) پیشنهاد بده. "
        f"عناوین نباید زرد باشند، بلکه کاربردی و مرتبط با سفال، سرامیک یا دکوراسیون باشند."
    )
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "titles": {"type": "ARRAY", "items": {"type": "STRING"}}
        },
        "required": ["titles"]
    }
    
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema}
    }
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                raise GeminiBlogError(f"API Error: {await resp.text()}")
            data = await resp.json()
            try:
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(content).get("titles", [])
            except:
                raise GeminiBlogError("خطا در پردازش عناوین تولید شده.")

# ==========================================
# ۲. نگارش مقاله با لینک‌سازی داخلی و خارجی
# ==========================================
async def generate_blog_article(title: str, products_context: list) -> dict:
    url = f"{_base()}/models/{_model()}:generateContent"
    headers = {"x-goog-api-key": _api_key(), "Content-Type": "application/json"}
    
    # ساخت متن زمینه از محصولات سایت برای لینک‌سازی داخلی
    context_str = "\n".join([f"- {p['name']} (URL: {p['url']})" for p in products_context])
    
    prompt = f"""
    عنوان مقاله: "{title}"
    برند: "شهر سفال"
    
    وظایف تو:
    ۱. یک مقاله جامع، جذاب و سئوشده (حدود ۸۰۰ تا ۱۰۰۰ کلمه) برای وبلاگ بنویس.
    ۲. از تگ‌های HTML شامل <h2>, <h3>, <p>, <strong>, <ul>, <li> استفاده کن. استایل CSS درون‌خطی نده.
    ۳. **لینک‌سازی داخلی (بسیار مهم):** در طول متن، حداقل به ۳ مورد از محصولات زیر به صورت طبیعی لینک بده. 
       فرمت: <a href="URL">نام یا کلمه کلیدی مرتبط</a>
       محصولات مجاز برای لینک‌سازی:
       {context_str}
    ۴. **لینک‌سازی خارجی:** یک لینک خروجی Nofollow به یک منبع معتبر جهانی (مثل ویکی‌پدیا) درباره مفاهیم پایه (مثل تاریخچه سفال یا مواد سرامیک) در یک جای طبیعی از متن قرار بده. 
       فرمت: <a href="..." rel="nofollow" target="_blank">کلمه</a>
    ۵. پاراگراف‌ها کوتاه و خوانا باشند. محتوا نباید کپی یا رباتی به نظر برسد.
    """
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "focus_keyword": {"type": "STRING", "description": "کلمه کلیدی اصلی (۲ تا ۴ کلمه)"},
            "seo_title": {"type": "STRING", "description": "عنوان سئو (حداکثر ۶۵ کاراکتر)"},
            "meta_description": {"type": "STRING", "description": "توضیحات متا (حدود ۱۴۰ کاراکتر)"},
            "slug": {"type": "STRING", "description": "نامک انگلیسی با خط تیره (kebab-case)"},
            "content_html": {"type": "STRING", "description": "محتوای کامل HTML مقاله همراه با لینک‌ها"}
        },
        "required": ["focus_keyword", "seo_title", "meta_description", "slug", "content_html"]
    }
    
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "maxOutputTokens": 8000}
    }
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                raise GeminiBlogError(f"API Error: {await resp.text()}")
            data = await resp.json()
            try:
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(content)
            except:
                raise GeminiBlogError("خطا در پردازش مقاله تولید شده.")
