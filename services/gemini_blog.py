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
# ۲. نگارش مقاله با عکس محصولات و سئوی پیشرفته (کار سنگین)
# ==========================================
async def generate_blog_article(title: str, products_context: list) -> dict:
    # ساخت متن زمینه شامل نام، لینک و آدرس عکس محصولات
    context_str = ""
    for p in products_context:
        context_str += f"- نام: {p['name']} | لینک: {p['url']} | عکس: {p.get('image', '')}\n"
    
    prompt = f"""
    پیش از تولید محتوا، مانند یک نویسنده ارشد ابتدا در ذهن خود استدلال کن، عیب‌های احتمالی (مثل کمبود کلمات، افت سئو یا خرابی لینک‌ها) را پیدا کرده و سپس کامل‌ترین، جامع‌ترین و بی‌نقص‌ترین نسخه ممکن را تولید کن.
    
    عنوان مقاله: "{title}"
    برند: "شهر سفال"
    
    وظایف و قوانین سئو (بسیار مهم):
    ۱. **طول و ساختار مقاله:** یک مقاله بسیار جامع، طولانی و عمیق (حداقل ۱۳۰۰ کلمه) بنویس. برای رسیدن به این حجم، حتماً از حداقل ۷ تیتر اصلی (H2) و زیرتیترهای متعدد (H3) استفاده کن و توضیحات هر بخش را با جزئیات کامل و پاراگراف‌های طولانی بسط بده. 
    ۲. **استایل تیترها:** تمام تیترهای <h2> و <h3> باید با یک بولت (•) شروع شوند. (مثال: <h2>• اهمیت سفالگری</h2>)
    ۳. **سئوی پایه:** کلمه کلیدی اصلی باید حتماً در خط اول پاراگراف اول استفاده شود.
    ۴. **لینک‌سازی داخلی و تصاویر:** در لابه‌لای پاراگراف‌های مقاله، حداقل ۳ محصول مرتبط از لیست زیر را معرفی کن.
       برای هر محصول، حتماً عکس آن را با این فرمت دقیق HTML در وسط متن قرار بده و به آن لینک بده (استایل لینک‌ها نیمه‌بولد، فونت شیک و مرتب است):
       <a href="لینک_محصول" style="font-weight:600; color:#2c3e50; font-family:Tahoma, sans-serif; text-decoration:none;"><img src="عکس_محصول" alt="نام_محصول" style="display:block; margin:30px auto; max-width:350px; width:100%; border-radius:12px; box-shadow:0 4px 10px rgba(0,0,0,0.15);"></a>
       لیست محصولات برای انتخاب:
       {context_str}
    ۵. **لینک‌سازی خارجی:** فقط یک لینک خروجی به یک سایت معتبر بده. لینک باید Dofollow باشد و استایل متن لینک باید دقیقاً اینگونه باشد تا نیمه‌بولد و زیبا دیده شود:
       <a href="آدرس_سایت" style="font-weight:600; color:#2c3e50; font-family:Tahoma, sans-serif; text-decoration:none; border-bottom:1px dashed #2c3e50;">کلمه مورد نظر</a>
    ۶. **نامک (Slug):** نامک انگلیسی باید بسیار کوتاه و مرتبط باشد (حداکثر ۴۰ کاراکتر).
    ۷. **برچسب‌ها (Tags):** بین ۳ تا ۵ برچسب (تگ) کوتاه و کاملاً مرتبط برای مقاله تولید کن.
    """
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "ai_reasoning": {
                "type": "STRING", 
                "description": "ابتدا در این فیلد استدلال کن: آیا متن به 1300 کلمه می‌رسد؟ آیا تیترها • دارند؟ آیا لینک‌ها سالمند؟ عیب‌یابی کن."
            },
            "focus_keyword": {"type": "STRING"},
            "seo_title": {"type": "STRING"},
            "meta_description": {"type": "STRING"},
            "slug": {"type": "STRING"},
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "content_html": {"type": "STRING"}
        },
        "required": ["ai_reasoning", "focus_keyword", "seo_title", "meta_description", "slug", "tags", "content_html"]
    }
    
    return await _execute_waterfall_request(prompt, schema, task_type="heavy")
