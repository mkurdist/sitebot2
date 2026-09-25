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

async def _execute_waterfall_request(prompt: str, schema: dict, task_type: str = "heavy") -> dict:
    keys = _api_keys()
    if not keys: raise GeminiBlogError("هیچ کلیدی تنظیم نشده است.")

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
# ۱. تولید عناوین جذاب با در نظر گرفتن کلمات کلیدی ممنوعه
# ==========================================
async def generate_blog_titles(topic: str, locked_keywords: list = None) -> list:
    locked_str = ""
    if locked_keywords:
        locked_str = "هشدار سئو: کلمات کلیدی زیر قبلاً در سایت استفاده شده‌اند. به هیچ وجه نباید از این کلمات به عنوان تمرکز اصلی عناوین استفاده کنی:\n"
        locked_str += ", ".join(locked_keywords) + "\n\n"

    prompt = (
        f"تو یک متخصص سئو و کپی‌رایتر سایت 'شهر سفال' هستی. "
        f"{locked_str}"
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
# ۲. نگارش مقاله فوق‌پیشرفته (ارتقایافته)
# ==========================================
async def generate_blog_article(title: str, all_products: list, locked_keywords: list = None) -> dict:
    # 🌟 تزریق آیدی محصول به کاتالوگ برای رهگیری بک‌لینک‌ها
    catalog_str = ""
    for p in all_products:
        catalog_str += f"- آیدی: {p['product_id']} | نام: {p['name']} | لینک: {p['permalink']} | عکس: {p['image_url']}\n"

    locked_str = ""
    if locked_keywords:
        locked_str = "کلمات کلیدی ممنوعه (برای جلوگیری از همنوع‌خواری):\n" + ", ".join(locked_keywords) + "\n"
    
    prompt = f"""
    پیش از تولید محتوا، مانند یک نویسنده ارشد ابتدا در ذهن خود استدلال کن، عیب‌های احتمالی را پیدا کرده و سپس کامل‌ترین نسخه ممکن را تولید کن.
    
    عنوان مقاله: "{title}"
    برند: "شهر سفال"
    {locked_str}
    
    وظایف و قوانین سئو (بسیار مهم):
    ۱. **طول و ساختار مقاله:** یک مقاله بسیار جامع، طولانی و عمیق (حداقل ۱۳۰۰ کلمه) بنویس. برای رسیدن به این حجم، حتماً از حداقل ۷ تیتر اصلی (H2) و زیرتیترهای متعدد (H3) استفاده کن. 
    ۲. **استایل تیترها:** تمام تیترهای <h2> و <h3> باید با یک بولت (•) شروع شوند.
    ۳. **سئوی پایه:** کلمه کلیدی اصلی باید حتماً در خط اول پاراگراف اول استفاده شود. این کلمه نباید جزو کلمات ممنوعه بالا باشد.
    ۴. **لینک‌سازی داخلی هوشمند و ضد-توهم:** متن را تحلیل کن و حداقل ۳ محصول کاملاً مرتبط را از کاتالوگ زیر پیدا کن. به هیچ وجه آدرس لینک یا عکس را از خودت نساز؛ دقیقاً باید همانی باشد که در کاتالوگ است.
       برای هر محصول انتخاب‌شده، عکس آن را با این فرمت دقیق HTML در وسط متن قرار بده:
       <a href="لینک_محصول" style="font-weight:600; color:#2c3e50; font-family:Tahoma, sans-serif; text-decoration:none;"><img src="عکس_محصول" alt="نام_محصول" style="display:block; margin:30px auto; max-width:350px; width:100%; border-radius:12px; box-shadow:0 4px 10px rgba(0,0,0,0.15);"></a>
       کاتالوگ محصولات سایت:
       {catalog_str}
    ۵. **بخش سوالات متداول (FAQ):** 🌟 در انتهای مقاله، یک تیتر H2 با عنوان "• سوالات متداول" ایجاد کن و ۳ سوال پرتکرار کاربران درباره این موضوع را با تگ H3 و جواب‌های کوتاه (پاراگراف) پاسخ بده.
    ۶. **لینک‌سازی خارجی:** فقط یک لینک خروجی Dofollow به یک سایت معتبر بده با استایل: <a href="آدرس" style="font-weight:600; color:#2c3e50; font-family:Tahoma, sans-serif; text-decoration:none; border-bottom:1px dashed #2c3e50;">کلمه</a>
    ۷. **نامک و برچسب‌ها:** نامک انگلیسی کوتاه (حداکثر ۴۰ کاراکتر) و بین ۳ تا ۵ برچسب مرتبط تولید کن.
    """
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "ai_reasoning": {
                "type": "STRING", 
                "description": "استدلال: آیا کلمه کلیدی جدید است؟ ۱۳۰۰ کلمه رعایت شد؟ آیا لینک‌ها دقیقاً از کاتالوگ کپی شده‌اند؟"
            },
            "focus_keyword": {"type": "STRING"},
            "seo_title": {"type": "STRING"},
            "meta_description": {"type": "STRING"},
            "slug": {"type": "STRING"},
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "used_product_ids": {  # 🌟 استخراج هوشمند آیدی محصولات انتخاب شده
                "type": "ARRAY", 
                "items": {"type": "INTEGER"},
                "description": "لیست دقیق آیدی (product_id) محصولاتی که از کاتالوگ انتخاب و در متن استفاده کردی."
            },
            "content_html": {"type": "STRING"}
        },
        "required": ["ai_reasoning", "focus_keyword", "seo_title", "meta_description", "slug", "tags", "used_product_ids", "content_html"]
    }
    
    return await _execute_waterfall_request(prompt, schema, task_type="heavy")
