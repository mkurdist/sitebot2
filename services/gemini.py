import asyncio
import base64
import html
import json
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
import aiohttp

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def _api_keys() -> list:
    keys_str = os.getenv("GEMINI_API_KEYS", "")
    if not keys_str:
        keys_str = os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def _model() -> str: return os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
def _base() -> str: return os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
def _max_tokens() -> int: return int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "16384"))
def _max_attempts() -> int: return max(1, int(os.getenv("GEMINI_MAX_ATTEMPTS", "4")))
def is_configured() -> bool: return bool(_api_keys())

REQUEST_TIMEOUT = 240
RETRY_DELAYS = [5, 15, 40]

class GeminiError(Exception): pass
class GeminiParseError(GeminiError): pass

# ==========================================
# پرامپت سیستمی
# ==========================================
SYSTEM_PROMPT = """
تو یک متخصص ارشد SEO، کپی‌رایتینگ فروشگاهی، تولید محتوای محصول و WooCommerce هستی.
وظیفه‌ات: با دریافت عکس(های) یک محصول و مشخصات خام آن، برای «همان یک محصول» محتوایی کامل، حرفه‌ای، طبیعی، یونیک و SEO-Friendly تولید کنی.

برند فروشگاه: «سیتی سفال» — نام انگلیسی: CITY SOFAL.

## قانون اطلاعات محصول
- هرچه کاربر می‌دهد اطلاعات واقعی است. هرچه نداده، حدس نزن.
- اطلاعات نامشخص را در جدول مشخصات دقیقاً «اعلام نشده» بنویس.

## Focus Keyword
- یک کلمه‌ی کلیدی اختصاصی، ۲ تا ۵ کلمه و کاملا مرتبط.
- چگالی کلمه کلیدی در متن اصلی (full_description_html) باید حدود ۱ درصد باشد (پخش شده در سراسر متن).

## حجم و کیفیت
- متن اصلی (full_description_html) به هیچ وجه نباید کمتر از ۷۵۰ کلمه باشد (ایده‌آل بین ۷۵۰ تا ۱۰۰۰ کلمه).
- استفاده از تگ‌های <h2> برای تیتربندی.

## برندینگ
نام‌های «سیتی سفال» یا «CITY SOFAL» را حداقل یک بار به صورت طبیعی در متن استفاده کن.

## ممنوع
آوردن هرگونه لینک، آیدی شبکه‌های اجتماعی، شماره تماس، و جملات کلیشه‌ای مثل "بهترین در ایران" اکیدا ممنوع است.

## قالب خروجی (فقط JSON)
- focus_keyword: کلمه‌ی کلیدی.
- title: عنوان محصول.
- short_description: توضیحات کوتاه.
- conversational_text: متن محاوره‌ای جذاب.
- full_description_html: HTML استاندارد فقط با تگ‌های <h2> <h3> <p> <strong> <ul> <li>.
- specs: ردیف‌های جدول مشخصات. **بسیار مهم: جدول تو باید دقیقاً شامل این ۷ سطر باشد و هیچ سطری اضافه یا کم نشود:**
  1. اسم محصول
  2. جنس
  3. رنگ
  4. وزن
  5. ابعاد
  6. ارسال
  7. بسته بندی
- meta_title: عنوان سئو.
- meta_description: توضیحات متا.
- slug: نامک انگلیسی.
- tags: چند برچسب فارسی مرتبط.
- image_alt_texts: تگ آلت به تعداد عکس‌های پیوست.
""".strip()

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "focus_keyword": {"type": "STRING"},
        "title": {"type": "STRING"},
        "short_description": {"type": "STRING"},
        "conversational_text": {"type": "STRING"},
        "full_description_html": {"type": "STRING"},
        "specs": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING"}, "value": {"type": "STRING"}},
                "required": ["name", "value"],
            },
        },
        "meta_title": {"type": "STRING"},
        "meta_description": {"type": "STRING"},
        "slug": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "image_alt_texts": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "focus_keyword", "title", "short_description", "conversational_text",
        "full_description_html", "specs", "meta_title", "meta_description",
        "slug", "tags", "image_alt_texts",
    ]
}

def normalize(s: str) -> str: return (s or "").replace("ي", "ی").replace("ك", "ک").replace("ـ", "")
def html_to_text(s: str) -> str:
    s = re.sub(r"</?strong[^>]*>", "", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "\n", s))
def _collapse(s: str) -> str: return re.sub(r"[ \t\u00a0]+", " ", normalize(s))
def count_occurrences(text: str, phrase: str) -> int:
    phrase = _collapse(phrase).strip()
    return _collapse(text).count(phrase) if phrase else 0
def word_count(text: str) -> int: return len([w for w in re.split(r"\s+", text or "") if re.search(r"\w", w)])
def _strip_markup(s: str) -> str: return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", s or "").replace("**", "").replace("__", "")).strip()

def sanitize_product(raw) -> dict:
    if not isinstance(raw, dict): raise GeminiParseError("ساختار خروجی معتبر نیست.")
    def s(key: str) -> str: v = raw.get(key, ""); return v.strip() if isinstance(v, str) else ""

    full = re.sub(r">\s+<", "><", re.sub(r"^```(?:html)?\s*|\s*```$", "", s("full_description_html")).strip())
    slug = re.sub(r"[^a-z0-9]+", "-", s("slug").lower()).strip("-")
    tags_raw = raw.get("tags", [])
    tags, seen = [], set()
    for t in (re.split(r"[،,]", tags_raw) if isinstance(tags_raw, str) else tags_raw) or []:
        t = str(t).strip()
        if t and normalize(t) not in seen:
            seen.add(normalize(t))
            tags.append(t)

    alts = [str(a).strip() for a in (raw.get("image_alt_texts", []) if isinstance(raw.get("image_alt_texts"), list) else [])]
    specs = [{"name": str(r.get("name", "")).strip(), "value": str(r.get("value", "")).strip()} for r in (raw.get("specs", []) if isinstance(raw.get("specs"), list) else []) if isinstance(r, dict) and str(r.get("name", "")).strip() and str(r.get("value", "")).strip()]

    return {
        "focus_keyword": _strip_markup(s("focus_keyword")), "title": _strip_markup(s("title")),
        "short_description": _strip_markup(s("short_description")), "conversational_text": _strip_markup(s("conversational_text")),
        "full_description_html": full, "specs": specs,
        "meta_title": _strip_markup(s("meta_title")), "meta_description": _strip_markup(s("meta_description")),
        "slug": slug, "tags": tags, "image_alt_texts": alts,
    }

@dataclass
class Validation:
    hard: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def validate_product(p: dict, specs_text: str, n_images: int, existing: Optional[list] = None) -> Validation:
    v = Validation()
    hard, warn = v.hard, v.warnings
    kw = p.get("focus_keyword", "")
    full_html = p.get("full_description_html", "")
    full_text = html_to_text(full_html)
    kw_count = count_occurrences(full_text, kw) if kw else 0
    
    v.stats = {
        "kw_count": kw_count, "full_words": word_count(full_text),
        "short_words": word_count(p.get("short_description", "")),
        "conv_words": word_count(p.get("conversational_text", "")),
        "meta_title_len": len(p.get("meta_title", "")),
        "meta_desc_len": len(p.get("meta_description", "")),
        "headings": len(re.findall(r"<h[23][\s>]", full_html, flags=re.I)),
    }

    if v.stats["full_words"] < 750: hard.append(f"متن خیلی کوتاه است ({v.stats['full_words']} کلمه). باید حداقل ۷۵۰ کلمه باشد.")
    
    target_kw = max(2, v.stats["full_words"] // 100)
    if kw_count == 0 and kw: hard.append("کلمه کلیدی اصلا در متن استفاده نشده است.")
    elif kw_count < (target_kw - 3) or kw_count > (target_kw + 5): warn.append(f"چگالی کلمه کلیدی با استاندارد ۱٪ فاصله دارد.")

    expected_specs = ["اسم محصول", "جنس", "رنگ", "وزن", "ابعاد", "ارسال", "بسته بندی"]
    actual_specs_norm = [normalize(r['name']) for r in p.get("specs", [])]
    expected_norm = [normalize(e) for e in expected_specs]
    missing = [e for e, en in zip(expected_specs, expected_norm) if en not in actual_specs_norm]
    extra = [a for a in p.get("specs", []) if normalize(a['name']) not in expected_norm]
    
    if len(p.get("specs", [])) != 7 or missing or extra:
        hard.append(f"جدول مشخصات باید دقیقاً و فقط شامل این ۷ ردیف باشد: ( {'، '.join(expected_specs)} ).")

    slug = p.get("slug", "")
    if slug and not _SLUG_RE.match(slug): warn.append("نامک (slug) از نظر ساختاری کمی نامعتبر است.")
    if len(p.get("image_alt_texts", [])) != n_images: warn.append("تعداد Alt تصاویر با تعداد عکس‌ها برابر نیست.")

    return v

def build_specs_table(rows: list) -> str:
    _TD, _TH = 'style="border:1px solid #ddd; padding:10px;"', 'style="border:1px solid #ddd; padding:10px; text-align:right;"'
    body = "".join(f"<tr><td {_TD}>{html.escape(r['name'])}</td><td {_TD}>{html.escape(r['value'])}</td></tr>" for r in rows)
    return f'<table style="width:100%; border-collapse:collapse;"><thead><tr><th {_TH}>ویژگی</th><th {_TH}>مشخصات</th></tr></thead><tbody>{body}</tbody></table>'

def _paragraph_html(text: str) -> str: return html.escape(text).replace("\n", "<br>")
def build_short_html(p: dict) -> str: return build_specs_table(p["specs"])
def build_description_html(p: dict) -> str:
    styled_full = re.sub(r"<p(?![a-zA-Z0-9])([^>]*)>", r'<p style="text-align: justify;"\1>', p["full_description_html"])
    return f'<div style="text-align: justify; line-height: 2;"><p style="text-align: justify; margin-bottom: 2em;">🔖 {_paragraph_html(p["short_description"])}</p><p style="text-align: justify; margin-bottom: 2em;"><em>🔖 {_paragraph_html(p["conversational_text"])}</em></p>{styled_full}</div>'

def build_preview_document(p: dict) -> bytes:
    title = html.escape(p["title"])
    return ("<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>body{{font-family:Tahoma,Arial,sans-serif;max-width:820px;margin:24px auto;line-height:2;padding:0 16px;text-align:justify}}h1{{font-size:22px}}small{{color:#777}}</style></head><body>"
            f"<h1>{title}</h1><small>پیش‌نمایش توضیح کوتاه</small>{build_short_html(p)}<hr>{build_description_html(p)}</body></html>").encode("utf-8")

# ==========================================
# استخر کلیدها و مسیریابی آبشاری (Enterprise Waterfall Routing)
# ==========================================
async def _post_generate(parts: list) -> dict:
    keys = _api_keys()
    if not keys: raise GeminiError("هیچ کلید API تنظیم نشده است.")

    primary_model = _model()
    lite_model = "gemini-3.5-flash-lite"
    models_to_try = [primary_model, lite_model] if primary_model != lite_model else [primary_model]
    
    last_error = "نامشخص"
    
    for model_name in models_to_try:
        for key in keys:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    url = f"{_base()}/models/{model_name}:generateContent"
                    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
                    body = {
                        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                        "contents": [{"role": "user", "parts": parts}],
                        "generationConfig": {"responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA, "maxOutputTokens": _max_tokens()}
                    }
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as session:
                        async with session.post(url, json=body, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                out = data["candidates"][0]["content"]["parts"][0]["text"]
                                return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", out).strip())
                            
                            # ارور ۴۲۹ (تکمیل ظرفیت) یا ارورهای دسترسی (۴۰۰، ۴۰۱، ۴۰۳، ۴۰۴)
                            # در این حالت نیازی به صبر کردن نیست، مستقیماً به کلید بعدی می‌رویم
                            if resp.status in (429, 400, 401, 403, 404):
                                last_error = f"Limit/Auth ({resp.status}) on {model_name} with key ending in {key[-4:]}"
                                break 
                            
                            # ارورهای ۵۰۰ سروری -> با همین کلید دوباره تلاش می‌کنیم
                            last_error = f"Server Error {resp.status}"
                except Exception as e:
                    last_error = f"Connection Error: {str(e)[:50]}"
                
                if attempt < len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt])

    raise GeminiError(f"تمامی کلیدها و مدل‌های جایگزین استفاده شدند اما پاسخی دریافت نشد.\nآخرین خطا: {last_error}")

def _repair_text(previous: dict, issues: list, instruction: Optional[str]) -> str:
    lines = ["", "=== اصلاح خروجی قبلی ===", "خروجی قبلی (JSON):", json.dumps(previous, ensure_ascii=False)]
    if instruction: lines += ["", f"دستور ویرایش ادمین (اولویت با این دستور است): {instruction}"]
    if issues: lines += ["", "مشکلات اندازه‌گیری‌شده‌ی خروجی قبلی که باید رفع شود:"] + [f"- {i}" for i in issues]
    return "\n".join(lines)

@dataclass
class GenResult:
    product: dict
    hard_issues: list
    warnings: list
    stats: dict
    attempts: int

async def generate_product(specs_text: str, images: list, *, existing: Optional[list] = None, instruction: Optional[str] = None, previous: Optional[dict] = None, on_progress: Optional[Callable[[str], Awaitable[None]]] = None) -> GenResult:
    base_text = f"مشخصات:\n{specs_text}\nمحصولات قبلی:\n{existing}"
    img_parts = [{"inlineData": {"mimeType": m, "data": base64.b64encode(d).decode("ascii")}} for d, m in images]
    
    best = None
    curr_prev, curr_iss, curr_inst = previous, [], instruction
    for attempt in range(1, _max_attempts() + 1):
        if on_progress: await on_progress(f"تلاش {attempt}...")
        text = base_text if not curr_prev else base_text + "\n" + _repair_text(curr_prev, curr_iss, curr_inst)
        
        try:
            raw = await _post_generate([{"text": text}] + img_parts)
            product = sanitize_product(raw)
            val = validate_product(product, specs_text, len(images), existing)
            res = GenResult(product, val.hard, val.warnings, val.stats, attempt)
            
            if not best or len(res.hard_issues) <= len(best.hard_issues): best = res
            if not val.hard: return res
            
            curr_prev, curr_iss, curr_inst = product, val.hard, instruction
        except Exception as e:
            if attempt == _max_attempts() and not best: raise e
            
    return best
