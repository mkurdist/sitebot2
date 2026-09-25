import os
import json
import re
import aiohttp
import asyncio
from dotenv import load_dotenv

load_dotenv()

def _api_keys() -> list:
    keys_str = os.getenv("GEMINI_API_KEYS", "")
    if not keys_str:
        keys_str = os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def _model() -> str: return os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
def _base() -> str: return os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

class GeminiBlogError(Exception): pass
RETRY_DELAYS = [5, 15, 40]

# متد مشترک ارسال با سیستم استخر کلید و مسیریابی آبشاری
async def _execute_waterfall_request(prompt: str, schema: dict, task_type: str = "heavy") -> dict:
    keys = _api_keys()
    if not keys: raise GeminiBlogError("هیچ کلیدی تنظیم نشده است.")

    # تخصیص هوشمند وظایف: کارهای سبک فقط به مدل لایت می‌روند.
    primary_model = _model()
    lite_model = "gemini-3.5-flash-lite"
    
    if task_type == "light":
        models_to_try = [lite_model]
    else:
        models_to_try = [primary_model, lite_model] if primary_model != lite_model else [primary_model]

    last_error = "نامشخص"
    
    for model_name in models_to_try:
        for key in keys:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    url = f"{_base()}/models/{model_name}:generateContent"
                    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
                    body = {
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "maxOutputTokens": 8000}
                    }
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
                        async with session.post(url, json=body, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                out = data["candidates"][0]["content"]["parts"][0]["text"]
                                return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", out).strip())
                            
                            if resp.status in (429, 400, 401, 403, 404):
                                last_error = f"Limit/Auth ({resp.status}) on {model_name}"
                                break 
                            
                            last_error = f"Server Error {resp.status}"
                except Exception as e:
                    last_error = f"Connection Error: {str(e)[:50]}"
                
                if attempt < len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt])

    raise GeminiBlogError(f"خطای سراسری در تمام کلیدها و مدل‌ها: {last_error}")


# ==========================================
# ۱. تولید عناوین جذاب (کار سبک -> فقط ارسال به مدل Lite)
# ==========================================
async def generate_blog_titles(topic: str) -> list:
    prompt = (
        f"تو یک متخصص سئو و کپی‌رایتر سایت 'شهر سفال' هستی. "
        f"برای موضوع '{topic}' دقیقاً ۵ عنوان مقاله بسیار جذاب، کلیک‌خور و سئوشده (بین ۵۰ تا ۶۵ کاراکتر) پیشنهاد بده. "
        f"عناوین نباید زرد باشند، بلکه کاربردی و مرتبط با سفال، سرامیک یا دکوراسیون باشند."
    )
    schema = {
        "type": "OBJECT",
        "properties": {"titles": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["titles"]
    }
    
    result = await _execute_waterfall_request(prompt, schema, task_type="light")
    return result.get("titles", [])

# ==========================================
# ۲. نگارش مقاله با لینک‌سازی (کار سنگین -> ارسال به مدل اصلی و در صورت نیاز Fallback)
# ==========================================
async def generate_blog_article(title: str, products_context: list) -> dict:
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
    ۴. **لینک‌سازی خارجی:** یک لینک خروجی Nofollow به یک منبع معتبر جهانی (مثل ویکی‌پدیا) درباره مفاهیم پایه در یک جای طبیعی از متن قرار بده. 
       فرمت: <a href="..." rel="nofollow" target="_blank">کلمه</a>
    ۵. پاراگراف‌ها کوتاه و خوانا باشند. محتوا نباید کپی یا رباتی به نظر برسد.
    """
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "focus_keyword": {"type": "STRING"},
            "seo_title": {"type": "STRING"},
            "meta_description": {"type": "STRING"},
            "slug": {"type": "STRING"},
            "content_html": {"type": "STRING"}
        },
        "required": ["focus_keyword", "seo_title", "meta_description", "slug", "content_html"]
    }
    
    return await _execute_waterfall_request(prompt, schema, task_type="heavy")
