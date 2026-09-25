import asyncio
import html
import io
import time

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from utils.security import MENU_BUTTONS 
from services import gemini as gm
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service
from handlers.products import get_dashboard_keyboard

router = Router()

MAX_IMAGES = 8
MAX_DOC_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 18 * 1024 * 1024
DRAFT_TTL = 6 * 3600
DEBOUNCE_SECONDS = 1.5

_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

class GeminiWizard(StatesGroup):
    collecting = State()               
    reviewing = State()                
    waiting_for_instruction = State()  

_drafts: dict = {}

def _new_draft(chat_id: int) -> dict:
    return {
        "chat_id": chat_id, "ts": time.time(),
        "images": [], "texts": [],
        "status_msg_id": None, "status_task": None,
        "busy": False, "result": None,        
        "review_msg_id": None, "existing": None,
        "media": {}, "created_id": None,
    }

def _drop_draft(uid: int):
    d = _drafts.pop(uid, None)
    if d and d.get("status_task"): d["status_task"].cancel()

def _gc():
    now = time.time()
    for uid in [u for u, d in _drafts.items() if not d["busy"] and now - d["ts"] > DRAFT_TTL]:
        _drop_draft(uid)

def _get_draft(uid: int):
    _gc()
    d = _drafts.get(uid)
    if d: d["ts"] = time.time()
    return d

async def _safe_edit(msg, text: str, **kw):
    try: return await msg.edit_text(text, **kw)
    except Exception: return None

async def _set_kb(bot: Bot, chat_id: int, message_id, kb):
    if not message_id: return
    try: await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception: pass

def _collect_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 تولید محتوا با Gemini", callback_data="gp_generate")],
        [
            InlineKeyboardButton(text="🧹 پاک‌کردن ورودی‌ها", callback_data="gp_reset"),
            InlineKeyboardButton(text="❌ لغو", callback_data="gp_cancel"),
        ],
    ])

def _review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ساخت محصول در سایت", callback_data="gp_confirm")],
        [
            InlineKeyboardButton(text="🔄 تولید مجدد", callback_data="gp_regen"),
            InlineKeyboardButton(text="✏️ اصلاح با دستور", callback_data="gp_edit"),
        ],
        [InlineKeyboardButton(text="❌ لغو", callback_data="gp_cancel")],
    ])

@router.message(Command("gemini"))
@router.message(F.text == "🤖 محصول با Gemini")
async def gp_start(message: Message, state: FSMContext):
    if not gm.is_configured():
        await message.answer("❌ متغیر محیطی <code>GEMINI_API_KEY</code> تنظیم نشده است.", parse_mode="HTML")
        return
    uid = message.from_user.id
    _drop_draft(uid)
    await state.clear()
    _drafts[uid] = _new_draft(message.chat.id)
    await state.set_state(GeminiWizard.collecting)
    await message.answer(
        "🤖 <b>افزودن محصول با Gemini</b>\n\n"
        "۱) عکس(های) محصول را بفرستید (تا ۸ عکس؛ آلبوم هم قبول است).\n"
        "۲) مشخصات خام محصول را بفرستید.\n\n"
        "این جریان مخصوص <b>یک محصول در هر بار</b> است. برای خروج: /cancel",
        parse_mode="HTML",
    )

@router.message(F.text == "/cancel", StateFilter(GeminiWizard))
async def gp_cancel_command(message: Message, state: FSMContext):
    d = _drafts.get(message.from_user.id)
    if d and d["busy"]:
        await message.answer("⏳ در حال انجام عملیات هستم؛ چند لحظه بعد /cancel را بزنید.")
        return
    _drop_draft(message.from_user.id)
    await state.clear()
    await message.answer("❌ عملیات لغو شد.")

def _status_text(d: dict) -> str:
    n = len(d["images"])
    words = sum(len(t.split()) for t in d["texts"])
    lines = [
        "📥 <b>ورودی‌های دریافت‌شده</b>",
        f"🖼 عکس: <b>{n}</b> (حداکثر {MAX_IMAGES})",
        f"📝 متن مشخصات: <b>{len(d['texts'])}</b> پیام ({words} کلمه)",
        "",
    ]
    if n == 0: lines.append("⚠️ حداقل یک عکس لازم است.")
    elif not d["texts"]: lines.append("ℹ️ مشخصاتی نفرستاده‌اید.")
    else: lines.append("اگر چیز دیگری مانده بفرستید؛ وگرنه دکمه‌ی تولید را بزنید.")
    return "\n".join(lines)

def _schedule_status(bot: Bot, uid: int):
    d = _drafts.get(uid)
    if not d: return
    if d["status_task"]: d["status_task"].cancel()
    d["status_task"] = asyncio.create_task(_send_status_later(bot, uid))

async def _send_status_later(bot: Bot, uid: int):
    try: await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError: return
    d = _drafts.get(uid)
    if not d: return
    old = d["status_msg_id"]
    try:
        sent = await bot.send_message(d["chat_id"], _status_text(d), reply_markup=_collect_kb(), parse_mode="HTML")
        d["status_msg_id"] = sent.message_id
        if old:
            try: await bot.delete_message(d["chat_id"], old)
            except Exception: pass
    except Exception: pass

@router.message(GeminiWizard.collecting, F.photo | F.document)
async def gp_collect_media(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    d = _get_draft(uid)
    if not d:
        await state.clear()
        return
    if len(d["images"]) >= MAX_IMAGES: return
    
    if message.photo:
        ph = message.photo[-1]
        item = {"file_id": ph.file_id, "ext": "jpg", "mime": "image/jpeg", "bytes": None}
    else:
        doc = message.document
        name = (doc.file_name or "").lower()
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        if ext not in _MIME and doc.mime_type in ("image/jpeg", "image/png", "image/webp"):
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[doc.mime_type]
        if ext not in _MIME: return
        item = {"file_id": doc.file_id, "ext": ext, "mime": _MIME[ext], "bytes": None}

    d["images"].append(item)
    if message.caption and message.caption.strip() and message.caption.strip() not in d["texts"]:
        d["texts"].append(message.caption.strip())
    d["result"] = None
    _schedule_status(bot, uid)

@router.message(GeminiWizard.collecting, F.text, ~F.text.in_(MENU_BUTTONS))
async def gp_collect_text(message: Message, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    d = _get_draft(uid)
    if not d: return
    text = message.text.strip()
    if text.startswith("/"): return
    d["texts"].append(text)
    d["result"] = None
    _schedule_status(bot, uid)

@router.callback_query(F.data == "gp_reset", GeminiWizard.collecting)
async def gp_reset(callback: CallbackQuery):
    d = _get_draft(callback.from_user.id)
    if not d or d["busy"]: return
    d["images"].clear()
    d["texts"].clear()
    d["result"] = None
    await callback.message.edit_text("🧹 ورودی‌ها پاک شد.")

async def _download(bot: Bot, file_id: str) -> bytes:
    info = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(info.file_path, buf)
    return buf.getvalue()

async def _ensure_downloaded(bot: Bot, d: dict):
    for item in d["images"]:
        if item["bytes"] is None:
            item["bytes"] = await _download(bot, item["file_id"])

async def _existing_products() -> list:
    try:
        items = await wc_service.get_latest_products(per_page=30)
        return [{"name": p.get("name", "")} for p in items or []]
    except Exception: return []

async def _send_preview(bot: Bot, d: dict):
    res = d["result"]
    await bot.send_document(
        d["chat_id"],
        BufferedInputFile(gm.build_preview_document(res.product), filename="preview.html"),
        caption="📄 پیش‌نمایش HTML آماده است."
    )
    sent = await bot.send_message(d["chat_id"], f"🤖 <b>آماده تایید:</b>\n{res.product['title']}", reply_markup=_review_kb(), parse_mode="HTML")
    d["review_msg_id"] = sent.message_id

async def _run_generation(bot: Bot, state: FSMContext, uid: int, wait_msg: Message, *, instruction=None, previous=None, avoid=None):
    d = _drafts[uid]
    d["busy"] = True
    try:
        await _ensure_downloaded(bot, d)
        existing = await _existing_products()
        async def progress(text: str): await _safe_edit(wait_msg, f"⏳ {text}")
        
        res = await gm.generate_product(
            "\n".join(d["texts"]),
            [(i["bytes"], i["mime"]) for i in d["images"]],
            existing=existing, instruction=instruction, previous=previous, on_progress=progress
        )
        d["result"] = res
        d["existing"] = existing
        d["media"] = {}
        await state.set_state(GeminiWizard.reviewing)
        await _send_preview(bot, d)
    except Exception as e:
        await _safe_edit(wait_msg, f"❌ <b>خطا:</b>\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
        await state.set_state(GeminiWizard.collecting)
    finally:
        d["busy"] = False

@router.callback_query(F.data == "gp_generate", GeminiWizard.collecting)
async def gp_generate(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    d = _get_draft(uid)
    if not d or d["busy"] or not d["images"]: return
    await callback.answer()
    if d["status_task"]: d["status_task"].cancel()
    wait_msg = await callback.message.answer("⏳ در حال آماده‌سازی...")
    await _run_generation(bot, state, uid, wait_msg)

@router.callback_query(F.data == "gp_regen", StateFilter(GeminiWizard.reviewing, GeminiWizard.waiting_for_instruction))
async def gp_regen(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    d = _get_draft(uid)
    if not d or d["busy"]: return
    wait_msg = await callback.message.answer("⏳ در حال تولید مجدد...")
    await _run_generation(bot, state, uid, wait_msg)

@router.callback_query(F.data == "gp_confirm", StateFilter(GeminiWizard.reviewing))
async def gp_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    d = _get_draft(uid)
    if not d or d["busy"]: return
    d["busy"] = True
    p = d["result"].product
    n = len(d["images"])
    wait_msg = await callback.message.answer("⏳ در حال آپلود تصاویر با نام‌های سئوشده و ایجاد محصول...")

    try:
        # دریافت نامک تولید شده توسط هوش مصنوعی
        slug = p.get("slug", "product")
        alt_texts = p.get("image_alt_texts", [])
        
        # 🌟 آپلود و سئوی پویای تصاویر (نام‌گذاری به صورت slug-1, slug-2, ...)
        for i, img in enumerate(d["images"]):
            if i in d["media"]: continue
            img_title = f"{slug}-{i + 1}"
            
            # استخراج امن تگ Alt برای هر عکس
            alt_text = alt_texts[i] if i < len(alt_texts) else f"{p.get('title')} - تصویر {i+1}"
            
            d["media"][i] = await wp_service.upload_media(
                img["bytes"], 
                f"{img_title}.{img['ext']}", 
                alt_text, 
                img_title
            )

        # 🌟 فرمت‌بندی آرایه تصاویر برای ووکامرس (اولی = عکس اصلی / بقیه = گالری)
        images_payload = [{"id": d["media"][i]} for i in range(n)]
        
        payload = {
            "name": p["title"], "type": "simple", "status": "draft", "slug": p["slug"],
            "short_description": gm.build_short_html(p), "description": gm.build_description_html(p),
            "tags": [{"name": t} for t in p["tags"]], "images": images_payload,
            "meta_data": [
                {"key": "rank_math_focus_keyword", "value": p["focus_keyword"]},
                {"key": "rank_math_title", "value": p["meta_title"]},
                {"key": "rank_math_description", "value": p["meta_description"]},
            ],
        }
        
        result = await wc_service.create_simple_product(payload)
        await state.clear()
        await state.update_data(product_id=result["id"], product_name=p["title"])
        _drop_draft(uid)
        
        await wait_msg.edit_text(
            f"✅ <b>محصول به‌صورت پیش‌نویس ساخته شد!</b>\n"
            f"🖼 <b>مدیریت تصاویر:</b> عکس اول به عنوان اصلی و مابقی در گالری با نام سئوشده ذخیره شدند.\n\n"
            f"👇 از داشبورد قیمت و دسته‌بندی را مشخص کنید:",
            reply_markup=get_dashboard_keyboard(result["id"], p["title"]), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ارسال: {str(e)[:200]}")
    finally:
        if uid in _drafts: _drafts[uid]["busy"] = False

@router.callback_query(F.data == "gp_cancel", StateFilter(GeminiWizard.collecting, GeminiWizard.reviewing))
async def gp_cancel(callback: CallbackQuery, state: FSMContext):
    _drop_draft(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text("❌ عملیات لغو شد.")
