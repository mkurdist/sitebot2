"""
سرویس ایزوله‌ی Gemini برای ساخت محتوای محصول (CitySofal).

- به هیچ ماژول دیگری از پروژه وابسته نیست (نه config.py، نه ووکامرس).
- فقط از متغیرهای محیطی می‌خواند:
    GEMINI_API_KEY               (الزامی)
    GEMINI_MODEL                 (اختیاری، پیش‌فرض: gemini-3.6-flash)
    GEMINI_MAX_OUTPUT_TOKENS     (اختیاری، پیش‌فرض: 16384)
    GEMINI_MAX_ATTEMPTS          (اختیاری، پیش‌فرض: 4  → یک تولید + سه اصلاح خودکار)
- برای هر درخواست یک نشست aiohttp جدا می‌سازد؛ بنابراین نیازی به close() در bot.py نیست.
- کلید API فقط در هدر ارسال می‌شود (نه در URL) تا در لاگ‌ها ظاهر نشود.
"""
import asyncio
import base64
import html
import json
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import aiohttp

try:  # وقتی ماژول مستقل ایمپورت شود هم .env خوانده می‌شود (بی‌ضرر اگر قبلاً لود شده)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


# ==========================================
# تنظیمات (خواندن تنبل از env)
# ==========================================
def _api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()


def _base() -> str:
    return os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")


def _max_tokens() -> int:
    return int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "16384"))


def _max_attempts() -> int:
    return max(1, int(os.getenv("GEMINI_MAX_ATTEMPTS", "4")))


def is_configured() -> bool:
    return bool(_api_key())


REQUEST_TIMEOUT = 240          # ثانیه برای هر درخواست
RETRY_DELAYS = [5, 15, 40]     # تلاش مجدد برای 429 و خطاهای 5xx


class GeminiError(Exception):
    """خطای قابل‌نمایش به ادمین (پیام فارسی)."""


class GeminiParseError(GeminiError):
    """خروجی Gemini JSON معتبر نبود."""


# ==========================================
# پرامپت سیستمی (قوانین پرامپت اصلی + تبدیل خروجی به JSON)
# ==========================================
SYSTEM_PROMPT = """
تو یک متخصص ارشد SEO، کپی‌رایتینگ فروشگاهی، تولید محتوای محصول و WooCommerce هستی.
وظیفه‌ات: با دریافت عکس(های) یک محصول و مشخصات خام آن، برای «همان یک محصول» محتوایی کامل، حرفه‌ای، طبیعی، یونیک و SEO-Friendly تولید کنی که مستقیماً در WooCommerce و مارکت‌پلیس قابل استفاده باشد.

برند فروشگاه: «سیتی سفال» — نام انگلیسی: CITY SOFAL — نام هویتی: «سفال شهر».

## قانون بسیار مهم درباره اطلاعات محصول
- هرچه کاربر می‌دهد اطلاعات واقعی محصول است. هرچه نداده، حدس نزن.
- این موارد را هرگز اختراع نکن: ابعاد، ارتفاع، قطر، وزن، ظرفیت، جنس دقیق خاک، نوع لعاب، دمای پخت، نوع کوره، درصد مواد، مقاومت حرارتی، قابلیت ماشین ظرفشویی/مایکروویو/فر، ضدضربه، ضدخش، نشکن، دست‌ساز بودن، تعداد مراحل تولید.
- اطلاعات نامشخص را در جدول مشخصات دقیقاً «اعلام نشده» بنویس و در متن هم درباره‌اش ادعای قطعی نکن.
- «ضد آب» برای رنگ را به معنی ضدضربه، ضدخش، نشکن، مقاوم در برابر حرارت یا مناسب ماشین ظرفشویی تفسیر نکن؛ فقط همان ویژگی اعلام‌شده را بگو.
- «تضمین سلامت تا تحویل» فقط اگر کاربر اعلام کرده باشد. اشاره به بسته‌بندی ایمن/چندلایه/ضدضربه فقط اگر کاربر تأیید کرده باشد. از «صددرصد ضدضربه» و «غیرقابل شکست» هرگز استفاده نکن.
- نحوه فروش (تکی، ست ۶ عددی، ست ۱۲ عددی) فقط طبق گفته‌ی کاربر. اگر تعداد نگفته، چیزی اضافه نکن.
- زمان ارسال: اگر کاربر گفته همان را بنویس؛ اگر نگفته «۵ روز کاری».

## تحلیل عکس
قبل از نوشتن، عکس‌ها را دقیق ببین و فقط چیزهایی را که واقعاً دیده می‌شوند توصیف کن (رنگ، فرم، درپوش، دستگیره، تزئینات، نقطه‌کوبی، طرح، ترکیب رنگ، ظاهر سطح). چیزی که در تصویر قابل تشخیص نیست ادعا نکن. عکس جایگزین اطلاعات فنی واقعی نیست.

## Focus Keyword
- یک کلمه‌ی کلیدی اختصاصی، ۲ تا ۵ کلمه، طبیعی، قابل جستجو و دقیقاً مربوط به همین محصول (ترجیحاً نوع محصول + ویژگی متمایز، مثل «قندان نقطه کوبی مشکی»).
- کلمه‌ی خیلی عمومی مثل «قندان» ممنوع.
- باید با کلمه‌های کلیدی محصولات قبلی فروشگاه (اگر در پیام آمده) متفاوت باشد.
- در full_description_html باید **دقیقاً ۹ بار** با همان املا و همان فاصله‌ها بیاید؛ نه ۸ و نه ۱۰. تیترها و متن داخل <strong> هم شمرده می‌شوند. آن را با نیم‌فاصله، مترادف یا شکل نوشتاری دیگر دور نزن (آن نوشته‌ها شمرده نمی‌شوند).
- روش پیشنهادی: ۱ بار در پاراگراف اول، ۱ بار در یکی از تیترها، ۷ بار پراکنده در بقیه‌ی متن؛ در جاهای دیگر از عبارت‌هایی مثل «این قندان» یا «این محصول» استفاده کن. پس از نوشتن، تعداد را بشمار و اصلاح کن.

## حجم و کیفیت
- full_description_html بین ۸۵۰ تا ۱۰۰۰ کلمه (هدف: ۹۰۰ تا ۹۵۰) با حداقل ۵ تیتر <h2>. محتوای مفید و واقعی بنویس؛ برای رسیدن به تعداد کلمات جمله‌ی بی‌معنی، تکرار یا کش‌دادن نیاور.
- کیفیت و صحت اطلاعات از طولانی‌کردن متن مهم‌تر است.
- فارسی روان، انسانی و طبیعی؛ پاراگراف‌های کوتاه و مناسب موبایل؛ بدون کلیشه‌های تکراری مثل «اگر به دنبال محصولی خاص و منحصر به فرد هستید».
- متن باید نسبت به محصولات دیگر یونیک باشد: عنوان، کلمه کلیدی، توضیح کوتاه، متن محاوره‌ای، تیترها، زاویه معرفی، کاربرد، توصیف رنگ، پیشنهاد دکوراسیون، متا و Alt را اختصاصی بنویس. برای متن محاوره‌ای یک زاویه‌ی خاص انتخاب کن (تجربه استفاده، زیبایی میز پذیرایی، دکوراسیون، هدیه، حس سنتی، ترکیب با دکور مدرن، هنر نقطه‌کوبی، …).
- ساختار پیشنهادی تیترها (اجباری نیست): معرفی محصول، طراحی و ظاهر، رنگ و جزئیات تزئینی، جنس و ویژگی‌ها، کاربرد در پذیرایی، کاربرد در دکوراسیون، مناسب برای هدیه، راهنمای نگهداری و شستشو، بسته‌بندی و ارسال، جمع‌بندی.

## برندینگ
نام‌های «سیتی سفال»، «CITY SOFAL» و «سفال شهر» را غیرمستقیم و طبیعی به کار ببر (مثلاً «این محصول با هویت برند سیتی سفال ارائه می‌شود»). هر دو نام «سیتی سفال» و «CITY SOFAL» باید حداقل یک بار در full_description_html بیایند. از تکرار زیاد نام برند پرهیز کن. تبلیغ مستقیم ممنوع.

## قوانین پلتفرم‌های فروش (بسیار مهم)
در هیچ‌جای خروجی نیاور: URL، لینک، آدرس سایت، شماره تلفن، آیدی تلگرام/اینستاگرام/شبکه اجتماعی، QR، لینک خارجی، آدرس فیزیکی، دعوت به خرید از سایت دیگر یا انتقال گفتگو به بیرون پلتفرم، و عباراتی مثل «از سایت ما خرید کنید»، «به سایت ما مراجعه کنید»، «برای خرید وارد سایت شوید»، «از لینک زیر خرید کنید».

## ممنوع
اطلاعات فنی، ابعاد و وزن جعلی؛ ادعای پزشکی؛ گواهی یا استاندارد جعلی؛ ادعای «بهترین»، «شماره یک»، «بی‌رقیب»، «صددرصد نشکن»؛ ادعای ضدضربه/ضدخش/مناسب ماشین ظرفشویی/مایکروویو/فر بدون اطلاعات کاربر.

## قالب خروجی (فقط JSON مطابق schema، بدون هیچ متن دیگر)
- focus_keyword: فقط خود کلمه‌ی کلیدی.
- title: عنوان SEO-Friendly و قابل کلیک (نوع محصول + ویژگی شاخص + رنگ/طرح + جنس/کاربرد). حداکثر حدود ۷۰ کاراکتر؛ می‌تواند با «| سیتی سفال» تمام شود.
- short_description: یک پاراگراف متن ساده (بدون HTML) بین ۶۰ تا ۸۰ کلمه؛ شامل نوع محصول، ویژگی مهم، رنگ/طرح، کاربرد و نام برند «سیتی سفال».
- conversational_text: متن ساده‌ی صمیمی و اعتمادساز، حدود ۱۲۰ تا ۱۸۰ کلمه، درباره‌ی ظاهر، تجربه استفاده، کاربرد، دکوراسیون، بسته‌بندی، زمان ارسال و (فقط در صورت اعلام کاربر) تضمین سلامت.
- full_description_html: HTML استاندارد فقط با تگ‌های <h2> <h3> <p> <strong> <ul> <li> (بدون جدول، بدون استایل، بدون Markdown، بدون ```).
- specs: ردیف‌های جدول مشخصات به‌صورت {name, value}. ویژگی‌های مناسب (در صورت وجود): نام محصول، برند، نام تجاری، نوع محصول، جنس، رنگ، طرح، نوع تزئین، نوع رنگ، ویژگی رنگ، ابعاد، وزن، کاربرد، قابلیت شستشو، نحوه فروش، زمان ارسال، تضمین سلامت، نوع بسته‌بندی. مقدار نامشخص: «اعلام نشده». (جدول HTML را سیستم می‌سازد؛ تو فقط ردیف‌ها را بده.)
- meta_title: کمتر از ۶۰ کاراکتر، شامل کلمه کلیدی، ترجیحاً با «| سیتی سفال» تمام شود.
- meta_description: حدود ۱۲۰ تا ۱۵۰ کاراکتر، طبیعی و جذاب، کلمه کلیدی یک بار، بدون اغراق و بدون هدایت به خارج از پلتفرم.
- slug: انگلیسی، lowercase، kebab-case، کوتاه، بدون عدد و کلمه‌ی اضافه (مثل black-dot-painted-sugar-bowl).
- tags: ۶ تا ۱۰ برچسب فارسی مرتبط.
- image_alt_texts: دقیقاً به تعداد عکس‌های پیوست، به ترتیب عکس‌ها؛ هر Alt توصیفی، طبیعی، متفاوت از بقیه، مناسب Google Images و بدون Keyword Stuffing.
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
    ],
    "propertyOrdering": [
        "focus_keyword", "title", "short_description", "conversational_text",
        "full_description_html", "specs", "meta_title", "meta_description",
        "slug", "tags", "image_alt_texts",
    ],
}

PRODUCT_KEYS = list(RESPONSE_SCHEMA["properties"].keys())


# ==========================================
# ابزارهای متن
# ==========================================
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ALLOWED_TAGS = {"h2", "h3", "p", "strong", "ul", "li"}


def normalize(s: str) -> str:
    """یکسان‌سازی حروف عربی/فارسی برای شمارش و مقایسه."""
    return (s or "").replace("ي", "ی").replace("ك", "ک").replace("ـ", "")


def html_to_text(s: str) -> str:
    """متن قابل‌مشاهده‌ی یک HTML؛ تگ <strong> درون‌خطی است و کلمه را نمی‌شکند."""
    s = re.sub(r"</?strong[^>]*>", "", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "\n", s)
    return html.unescape(s)


def _collapse(s: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", normalize(s))


def count_occurrences(text: str, phrase: str) -> int:
    phrase = _collapse(phrase).strip()
    return _collapse(text).count(phrase) if phrase else 0


def word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if re.search(r"\w", w)])


def _strip_markup(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("**", "").replace("__", "")
    return re.sub(r"[ \t]+", " ", s).strip()


# ==========================================
# پاک‌سازی خروجی خام Gemini
# ==========================================
def sanitize_product(raw) -> dict:
    if not isinstance(raw, dict):
        raise GeminiParseError("ساختار خروجی Gemini معتبر نیست.")

    def s(key: str) -> str:
        v = raw.get(key, "")
        return v.strip() if isinstance(v, str) else ""

    full = s("full_description_html")
    full = re.sub(r"^```(?:html)?\s*|\s*```$", "", full).strip()
    full = re.sub(r">\s+<", "><", full)

    slug = re.sub(r"[^a-z0-9]+", "-", s("slug").lower()).strip("-")

    tags_raw = raw.get("tags", [])
    if isinstance(tags_raw, str):
        tags_raw = re.split(r"[،,]", tags_raw)
    tags, seen = [], set()
    for t in tags_raw if isinstance(tags_raw, list) else []:
        t = str(t).strip()
        if t and normalize(t) not in seen:
            seen.add(normalize(t))
            tags.append(t)

    alts_raw = raw.get("image_alt_texts", [])
    alts = [str(a).strip() for a in alts_raw] if isinstance(alts_raw, list) else []

    specs = []
    for row in raw.get("specs", []) if isinstance(raw.get("specs"), list) else []:
        if isinstance(row, dict):
            name, value = str(row.get("name", "")).strip(), str(row.get("value", "")).strip()
            if name and value:
                specs.append({"name": name, "value": value})

    return {
        "focus_keyword": _strip_markup(s("focus_keyword")),
        "title": _strip_markup(s("title")),
        "short_description": _strip_markup(s("short_description")),
        "conversational_text": _strip_markup(s("conversational_text")),
        "full_description_html": full,
        "specs": specs,
        "meta_title": _strip_markup(s("meta_title")),
        "meta_description": _strip_markup(s("meta_description")),
        "slug": slug,
        "tags": tags,
        "image_alt_texts": alts,
    }


# ==========================================
# اعتبارسنجی (بازبینی داخلی پرامپت، این‌بار در کد)
# ==========================================
@dataclass
class Validation:
    hard: list = field(default_factory=list)       # باید رفع شود (باعث اصلاح خودکار می‌شود)
    warnings: list = field(default_factory=list)   # فقط به ادمین نشان داده می‌شود
    stats: dict = field(default_factory=dict)


_ABSOLUTE_CLAIMS = ["بهترین", "شماره یک", "بی‌رقیب", "بی رقیب", "صددرصد", "صد درصد", "۱۰۰ درصد", "غیرقابل شکست"]
_UNSUPPORTED_CLAIMS = [
    "ظرفشویی", "مایکروویو", "مایکرویو", "ضدخش", "ضد خش",
    "ضدضربه", "ضد ضربه", "نشکن", "دست‌ساز", "دستساز", "دست ساز", "ضد حرارت", "مقاوم در برابر حرارت",
]
_BAD_PHRASES = [
    "از سایت ما", "به سایت ما", "در سایت ما", "وارد سایت", "لینک زیر",
    "از وب‌سایت ما", "به وب‌سایت ما", "از وبسایت ما", "به وبسایت ما",
]
_URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9\-]+\.(?:com|ir|net|org|co|shop|store|info|me)\b)", re.I)
_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_product(p: dict, specs_text: str, n_images: int, existing: Optional[list] = None) -> Validation:
    v = Validation()
    hard, warn = v.hard, v.warnings

    for k in ("focus_keyword", "title", "short_description", "conversational_text",
              "full_description_html", "meta_title", "meta_description", "slug"):
        if not p.get(k):
            hard.append(f"فیلد «{k}» خالی است.")

    kw = p.get("focus_keyword", "")
    full_html = p.get("full_description_html", "")
    full_text = html_to_text(full_html)

    # --- Focus Keyword ---
    if kw:
        n_kw_words = len(kw.split())
        if not 2 <= n_kw_words <= 5:
            hard.append(f"کلمه کلیدی «{kw}» باید ۲ تا ۵ کلمه باشد (الان {n_kw_words} کلمه).")
        taken = {
            _collapse(k).strip()
            for e in existing or []
            for k in re.split(r"[،,]", str(e.get("keyword", "") or ""))
            if k.strip()
        }
        if _collapse(kw).strip() in taken:
            hard.append(f"کلمه کلیدی «{kw}» با یکی از محصولات قبلی فروشگاه یکی است؛ کلمه‌ی کلیدی متفاوتی انتخاب کن.")

    kw_count = count_occurrences(full_text, kw) if kw else 0
    full_words = word_count(full_text)
    short_words = word_count(p.get("short_description", ""))
    conv_words = word_count(p.get("conversational_text", ""))
    v.stats = {
        "kw_count": kw_count,
        "full_words": full_words,
        "short_words": short_words,
        "conv_words": conv_words,
        "meta_title_len": len(p.get("meta_title", "")),
        "meta_desc_len": len(p.get("meta_description", "")),
        "headings": len(re.findall(r"<h[23][\s>]", full_html, flags=re.I)),
    }

    if kw and kw_count != 9:
        hard.append(f"کلمه کلیدی «{kw}» در full_description_html {kw_count} بار آمده؛ باید دقیقاً ۹ بار باشد "
                    f"({'%d بار دیگر اضافه کن' % (9 - kw_count) if kw_count < 9 else '%d بار کم کن' % (kw_count - 9)}؛ نوشته‌های جایگزین شمرده نمی‌شوند).")
    if not 850 <= full_words <= 1000:
        hard.append(f"full_description_html {full_words} کلمه است؛ باید بین ۸۵۰ تا ۱۰۰۰ کلمه باشد "
                    f"({'حدود %d کلمه‌ی مفید اضافه کن' % (900 - full_words) if full_words < 850 else 'حدود %d کلمه کم کن' % (full_words - 950)}).")
    if v.stats["headings"] < 5:
        hard.append(f"full_description_html فقط {v.stats['headings']} تیتر دارد؛ حداقل ۵ تیتر <h2> لازم است.")

    bad_tags = sorted({t.lower() for t in re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", full_html)} - _ALLOWED_TAGS)
    if bad_tags:
        hard.append("تگ‌های غیرمجاز در full_description_html: " + "، ".join(bad_tags) + " (فقط h2 h3 p strong ul li مجاز است).")

    norm_full = normalize(full_text).lower()
    if full_html:
        if "city sofal" not in norm_full:
            hard.append("نام «CITY SOFAL» باید حداقل یک‌بار به‌طور طبیعی در full_description_html بیاید.")
        if "سیتی سفال" not in norm_full:
            hard.append("نام «سیتی سفال» باید حداقل یک‌بار به‌طور طبیعی در full_description_html بیاید.")

    # --- حجم بخش‌های دیگر ---
    if p.get("short_description") and not 60 <= short_words <= 80:
        hard.append(f"short_description {short_words} کلمه است؛ باید بین ۶۰ تا ۸۰ کلمه باشد.")
    if p.get("short_description") and "سیتی سفال" not in normalize(p["short_description"]):
        hard.append("short_description باید نام برند «سیتی سفال» را داشته باشد.")
    if p.get("conversational_text") and not 110 <= conv_words <= 190:
        hard.append(f"conversational_text {conv_words} کلمه است؛ باید حدود ۱۲۰ تا ۱۸۰ کلمه باشد.")

    # --- عنوان و سئو ---
    title = p.get("title", "")
    if len(title) > 90:
        hard.append(f"title {len(title)} کاراکتر است و خیلی طولانی است؛ کوتاه‌تر (حداکثر حدود ۷۰) بنویس.")
    elif len(title) > 70:
        warn.append(f"عنوان کمی بلند است ({len(title)} کاراکتر).")

    mt = p.get("meta_title", "")
    if mt and len(mt) >= 60:
        hard.append(f"meta_title {len(mt)} کاراکتر است؛ باید کمتر از ۶۰ کاراکتر باشد.")
    if mt and kw and count_occurrences(mt, kw) == 0:
        warn.append("Meta Title کلمه کلیدی دقیق را ندارد.")

    md = p.get("meta_description", "")
    if md and not 110 <= len(md) <= 160:
        hard.append(f"meta_description {len(md)} کاراکتر است؛ باید حدود ۱۲۰ تا ۱۵۰ کاراکتر باشد.")

    slug = p.get("slug", "")
    if slug and not _SLUG_RE.match(slug):
        hard.append("slug باید انگلیسی، lowercase و kebab-case باشد.")
    if len(slug) > 60:
        warn.append("Slug کمی بلند است.")

    if not 6 <= len(p.get("tags", [])) <= 10:
        hard.append(f"تعداد برچسب‌ها {len(p.get('tags', []))} است؛ باید ۶ تا ۱۰ باشد.")

    alts = p.get("image_alt_texts", [])
    if len(alts) != n_images:
        hard.append(f"تعداد image_alt_texts {len(alts)} است؛ باید دقیقاً {n_images} عدد (به تعداد عکس‌ها) باشد.")
    elif len({normalize(a) for a in alts}) != len(alts) or any(not a for a in alts):
        hard.append("Alt Textها باید خالی نباشند و همه با هم متفاوت باشند.")
    if any(len(a) > 125 for a in alts):
        warn.append("برخی Altها بلندتر از ۱۲۵ کاراکتر هستند.")

    if len(p.get("specs", [])) < 8:
        hard.append(f"جدول مشخصات فقط {len(p.get('specs', []))} ردیف دارد؛ حداقل ۸ ردیف لازم است (مقدار نامشخص: «اعلام نشده»).")

    # --- قوانین پلتفرم: لینک، تلفن، آیدی ---
    spec_vals = " ".join(f"{r['name']} {r['value']}" for r in p.get("specs", []))
    all_text = "\n".join([
        p.get("title", ""), p.get("short_description", ""), p.get("conversational_text", ""),
        full_text, p.get("meta_title", ""), p.get("meta_description", ""),
        " ".join(p.get("tags", [])), " ".join(alts), spec_vals,
    ])
    ascii_text = all_text.translate(_DIGITS)
    if _URL_RE.search(ascii_text):
        hard.append("در متن URL یا آدرس سایت دیده شد؛ همه‌ی لینک‌ها و آدرس‌های سایت را حذف کن.")
    if _HANDLE_RE.search(ascii_text):
        hard.append("در متن آیدی شبکه اجتماعی (@...) دیده شد؛ حذف کن.")
    if _LONG_NUMBER_RE.search(ascii_text):
        hard.append("در متن دنباله‌ی عددی بلند (شبیه شماره تلفن) دیده شد؛ حذف کن.")
    norm_all = normalize(all_text)
    found_bad = [ph for ph in _BAD_PHRASES if ph in norm_all]
    if found_bad:
        hard.append("عبارت‌های دعوت به خرید از سایت ممنوع است: " + "، ".join(found_bad))
    if re.search(r"تلگرام|اینستاگرام|واتساپ|t\.me", norm_all, flags=re.I):
        warn.append("نام شبکه اجتماعی در متن آمده؛ بررسی کن دعوت به خروج از پلتفرم نباشد.")

    # --- ادعاهای بدون پشتوانه (هشدار برای بررسی ادمین) ---
    spec_norm = normalize(specs_text or "")
    for term in _ABSOLUTE_CLAIMS:
        if term in norm_all:
            warn.append(f"ادعای اغراق‌آمیز «{term}» در متن آمده.")
    for term in _UNSUPPORTED_CLAIMS:
        if term in norm_all and term not in spec_norm:
            warn.append(f"«{term}» در متن آمده ولی در مشخصات شما نبوده؛ بررسی کن ادعای ساختگی نباشد.")

    return v


# ==========================================
# ساخت HTML نهایی برای ووکامرس
# ==========================================
_TD = 'style="border:1px solid #ddd; padding:10px;"'
_TH = 'style="border:1px solid #ddd; padding:10px; text-align:right;"'


def build_specs_table(rows: list) -> str:
    body = "".join(
        f"<tr><td {_TD}>{html.escape(r['name'])}</td><td {_TD}>{html.escape(r['value'])}</td></tr>"
        for r in rows
    )
    return (
        '<table style="width:100%; border-collapse:collapse;"><thead><tr>'
        f"<th {_TH}>ویژگی</th><th {_TH}>مشخصات</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _paragraph_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def build_short_html(p: dict) -> str:
    """جدول مشخصات محصول که کنار تصویر در کادر توضیحات کوتاه ووکامرس قرار می‌گیرد."""
    return build_specs_table(p["specs"])


def build_description_html(p: dict) -> str:
    """متن محاوره‌ای و توضیح کوتاه همراه با بوک‌مارک و فاصله‌ی ۲ سطری و استایل Justify."""
    styled_full = re.sub(r"<p(?![a-zA-Z0-9])([^>]*)>", r'<p style="text-align: justify;"\1>', p["full_description_html"])
    return (
        f'<div style="text-align: justify; line-height: 2;">'
        f'<p style="text-align: justify; margin-bottom: 2em;">🔖 {_paragraph_html(p["short_description"])}</p>'
        f'<p style="text-align: justify; margin-bottom: 2em;"><em>🔖 {_paragraph_html(p["conversational_text"])}</em></p>'
        f'{styled_full}'
        f'</div>'
    )


def build_preview_document(p: dict) -> bytes:
    title = html.escape(p["title"])
    doc = (
        "<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:Tahoma,Arial,sans-serif;max-width:820px;margin:24px auto;"
        "line-height:2;padding:0 16px;text-align:justify}h1{font-size:22px}small{color:#777}</style></head><body>"
        f"<h1>{title}</h1><small>پیش‌نمایش توضیح کوتاه</small>{build_short_html(p)}<hr>"
        f"{build_description_html(p)}</body></html>"
    )
    return doc.encode("utf-8")


# ==========================================
# تماس با API
# ==========================================
def _api_error_message(text: str) -> str:
    try:
        return str(json.loads(text).get("error", {}).get("message", text))[:300]
    except Exception:
        return text[:300]


def _extract_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise GeminiError("پاسخ API قابل‌خواندن نبود.")
    reason = (data.get("promptFeedback") or {}).get("blockReason")
    if reason:
        raise GeminiError(f"درخواست توسط فیلتر Gemini مسدود شد ({reason}). عکس/متن را تغییر بده.")
    cands = data.get("candidates") or []
    if not cands:
        raise GeminiError("پاسخی از Gemini دریافت نشد.")
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    out = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    finish = cand.get("finishReason")
    if not out:
        raise GeminiParseError(f"پاسخ Gemini خالی بود (finishReason={finish}).")
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out).strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        hint = " (خروجی به سقف توکن خورد؛ GEMINI_MAX_OUTPUT_TOKENS را بیشتر کن)" if finish == "MAX_TOKENS" else ""
        raise GeminiParseError(f"خروجی Gemini JSON معتبر نبود{hint}.")


async def _post_generate(parts: list) -> dict:
    key = _api_key()
    if not key:
        raise GeminiError("GEMINI_API_KEY در متغیرهای محیطی تنظیم نشده است.")

    url = f"{_base()}/models/{_model()}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "maxOutputTokens": _max_tokens(),
        },
    }
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    last_err = "خطای نامشخص"
    for attempt in range(len(RETRY_DELAYS) + 1):
        status, text = None, ""
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    status, text = resp.status, await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = f"خطای شبکه در ارتباط با Gemini ({type(e).__name__})."
        else:
            if status == 200:
                return _extract_json(text)
            msg = _api_error_message(text)
            if status in (429, 500, 502, 503, 504):
                last_err = f"Gemini موقتاً در دسترس نیست یا سهمیه تمام شده ({status}): {msg}"
            elif status == 404:
                raise GeminiError(f"مدل «{_model()}» پیدا نشد؛ مقدار GEMINI_MODEL را بررسی کن. ({msg})")
            elif status in (401, 403) or "API key" in msg:
                raise GeminiError(f"کلید Gemini پذیرفته نشد؛ GEMINI_API_KEY را بررسی کن. ({msg})")
            else:
                raise GeminiError(f"خطای Gemini ({status}): {msg}")
        if attempt < len(RETRY_DELAYS):
            await asyncio.sleep(RETRY_DELAYS[attempt])
    raise GeminiError(last_err)


# ==========================================
# ساخت پیام کاربر
# ==========================================
def _base_user_text(specs_text: str, n_images: int, existing: Optional[list]) -> str:
    lines = [
        f"تعداد عکس‌های پیوست: {n_images} (به ترتیب ارسال؛ image_alt_texts دقیقاً همین‌قدر باشد).",
        "",
        "مشخصات خام محصول (اطلاعات واقعی؛ فقط همین‌ها معتبرند):",
        "---",
        specs_text.strip() if specs_text.strip() else "(مشخصاتی ارسال نشده؛ فقط از روی عکس‌ها بنویس و هیچ ادعای فنی نکن.)",
        "---",
    ]
    if existing:
        lines += ["", "محصولات قبلی فروشگاه (عنوان | کلمه کلیدی) — کلمه کلیدی و زاویه‌ی محتوا را متفاوت انتخاب کن و متن را کپی نکن:"]
        for e in existing[:30]:
            lines.append(f"- {e.get('name', '')} | {e.get('keyword', '') or '—'}")
    return "\n".join(lines)


def _image_parts(images: list) -> list:
    return [
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}}
        for data, mime in images
    ]


def _repair_text(previous: dict, issues: list, instruction: Optional[str]) -> str:
    lines = ["", "=== اصلاح خروجی قبلی ===", "خروجی قبلی (JSON):",
             json.dumps(previous, ensure_ascii=False)]
    if instruction:
        lines += ["", f"دستور ویرایش ادمین (اولویت با این دستور است): {instruction}"]
    if issues:
        lines += ["", "مشکلات اندازه‌گیری‌شده‌ی خروجی قبلی که باید رفع شود:"] + [f"- {i}" for i in issues]
    lines += [
        "",
        "فقط همین موارد را اصلاح کن و بقیه‌ی محتوا را تا حد امکان حفظ کن. کل JSON را با همان ساختار برگردان.",
        "پس از اصلاح، تعداد تکرار کلمه کلیدی (دقیقاً ۹) و تعداد کلمات هر بخش را دوباره بشمار.",
    ]
    return "\n".join(lines)


# ==========================================
# نقطه‌ی ورود: تولید + اعتبارسنجی + اصلاح خودکار
# ==========================================
@dataclass
class GenResult:
    product: dict
    hard_issues: list
    warnings: list
    stats: dict
    attempts: int


ProgressCb = Callable[[str], Awaitable[None]]


async def generate_product(
    specs_text: str,
    images: list,                       # [(bytes, mime), ...]
    *,
    existing: Optional[list] = None,    # [{"name":..., "keyword":...}, ...]
    instruction: Optional[str] = None,  # دستور ویرایش ادمین
    previous: Optional[dict] = None,    # خروجی قبلی (برای حالت ویرایش)
    on_progress: Optional[ProgressCb] = None,
) -> GenResult:
    if not images:
        raise GeminiError("حداقل یک عکس لازم است.")

    n = len(images)
    max_attempts = _max_attempts()
    base_text = _base_user_text(specs_text, n, existing)
    img_parts = _image_parts(images)

    async def progress(msg: str):
        if on_progress:
            try:
                await on_progress(msg)
            except Exception:
                pass

    best: Optional[GenResult] = None
    current_prev, current_issues, current_instr = previous, [], instruction
    last_error: Optional[GeminiError] = None
    used = 0

    for attempt in range(1, max_attempts + 1):
        used = attempt
        if current_prev is None:
            text = base_text
        else:
            text = base_text + "\n" + _repair_text(current_prev, current_issues, current_instr)

        if attempt == 1:
            await progress("🧠 Gemini در حال تحلیل عکس‌ها و نوشتن محتواست…")
        else:
            await progress(f"🔧 اصلاح خودکار (تلاش {attempt} از {max_attempts}) — {len(current_issues)} مورد…")

        try:
            raw = await _post_generate([{"text": text}] + img_parts)
            product = sanitize_product(raw)
        except GeminiParseError as e:
            last_error = e
            current_issues = [str(e)]
            continue
        except GeminiError as e:
            if best is None and attempt == 1:
                raise
            last_error = e
            break

        val = validate_product(product, specs_text, n, existing)
        result = GenResult(product, val.hard, val.warnings, val.stats, attempt)
        if best is None or len(result.hard_issues) <= len(best.hard_issues):
            best = result
        if not val.hard:
            result.attempts = used
            return result

        current_prev, current_issues, current_instr = product, val.hard, instruction

    if best is None:
        raise last_error or GeminiError("تولید محتوا با Gemini ناموفق بود.")
    best.attempts = used
    return best
