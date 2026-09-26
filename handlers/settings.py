"""
🌟 ماژول کاملاً ایزوله «تنظیمات پیشرفته»

فقط همین فایل + سه فایل زیر services/ (settings_service, health_check,
scheduler_service) اضافه شده‌اند. هیچ فایل دیگری ادیت نشده جز دو خط ثبت
روتر در main و یک تغییر بسیار کوچک و افزایشی (additive) در:
  - utils/security.py  (برای پشتیبانی ادمین کمکی)
  - bot.py              (برای ارسال اعلان به کانال + اجرای زمان‌بند)
که هر دو در فایل‌های جدا و با کامنت 🌟 [ماژول تنظیمات] مشخص شده‌اند.

دکمه «⚙️ تنظیمات» از قبل در common.py و MENU_BUTTONS وجود داشت ولی هیچ
handler ای نداشت؛ این فایل همان دکمه را «وصل» می‌کند.
"""

from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_ID
from services.settings_service import settings_service
from services.health_check import run_health_check
from services.woocommerce import wc_service_instance as wc_service
from services.database import db_service

router = Router()


# ==========================================
# وضعیت‌های FSM (کاملاً مجزا از ویزاردهای دیگر پروژه)
# ==========================================
class SettingsFSM(StatesGroup):
    waiting_channel = State()
    waiting_admin_id = State()


NOTIF_STATUSES = [
    ("pending", "⏳ در انتظار پرداخت"),
    ("processing", "💳 پرداخت شده"),
    ("completed", "✅ تکمیل‌شده"),
    ("cancelled", "❌ لغوشده"),
    ("on_hold", "⏸ در انتظار بررسی"),
    ("failed", "⚠️ ناموفق"),
]


async def _safe_edit(msg, text: str, **kw):
    try:
        return await msg.edit_text(text, **kw)
    except Exception:
        return None


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩺 تست سلامت سیستم", callback_data="st:health")],
        [InlineKeyboardButton(text="🔔 مدیریت اعلان سفارش‌ها", callback_data="st:notif")],
        [InlineKeyboardButton(text="📢 اتصال کانال اعلان‌ها", callback_data="st:channel")],
        [InlineKeyboardButton(text="📊 آمار کلی فروشگاه", callback_data="st:stats")],
        [InlineKeyboardButton(text="⏰ زمان‌بندی خودکار Sync", callback_data="st:sync")],
        [InlineKeyboardButton(text="🚨 آخرین خطاها", callback_data="st:errors")],
        [InlineKeyboardButton(text="👥 مدیریت دسترسی", callback_data="st:admins")],
    ])


def _back_kb(extra_rows=None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات", callback_data="st:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================
# ورودی اصلی: همان دکمه‌ی «⚙️ تنظیمات» که قبلاً بی‌صاحب بود
# ==========================================
@router.message(F.text == "⚙️ تنظیمات")
async def settings_entry(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚙️ <b>پنل تنظیمات پیشرفته</b>\nیکی از بخش‌ها را انتخاب کنید:",
        reply_markup=_main_menu_kb(), parse_mode="HTML"
    )


@router.callback_query(F.data == "st:menu")
async def st_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await _safe_edit(
        callback.message,
        "⚙️ <b>پنل تنظیمات پیشرفته</b>\nیکی از بخش‌ها را انتخاب کنید:",
        reply_markup=_main_menu_kb(), parse_mode="HTML"
    )


# ==========================================
# ۱. تست سلامت سیستم
# ==========================================
@router.callback_query(F.data == "st:health")
async def st_health(callback: CallbackQuery):
    await callback.answer()
    await _safe_edit(callback.message, "⏳ در حال بررسی اتصال‌ها...")
    try:
        result = await run_health_check()
    except Exception as e:
        await settings_service.log_error("health_check", str(e))
        await _safe_edit(
            callback.message,
            f"❌ خطا در اجرای تست سلامت:\n<code>{str(e)[:200]}</code>",
            reply_markup=_back_kb(), parse_mode="HTML"
        )
        return

    for name, r in result.items():
        if not r["ok"]:
            await settings_service.log_error(f"health_{name}", r["detail"])

    def _line(name, r):
        icon = "✅" if r["ok"] else "❌"
        return f"{icon} {name}: {r['detail']}"

    text = (
        "🩺 <b>نتیجه تست سلامت سیستم</b>\n\n"
        f"{_line('ووکامرس', result['wc'])}\n"
        f"{_line('وردپرس', result['wp'])}\n"
        f"{_line('دیتابیس', result['db'])}"
    )
    await _safe_edit(callback.message, text, reply_markup=_back_kb(), parse_mode="HTML")


# ==========================================
# ۲. مدیریت اعلان سفارش‌ها
# ==========================================
async def _notif_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, label in NOTIF_STATUSES:
        enabled = await settings_service.get(f"notif_enabled_{key}")
        icon = "🟢" if enabled else "🔴"
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"st:notif:toggle:{key}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات", callback_data="st:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:notif")
async def st_notif(callback: CallbackQuery):
    await callback.answer()
    kb = await _notif_kb()
    await _safe_edit(
        callback.message,
        "🔔 <b>مدیریت اعلان سفارش‌ها</b>\n\nروی هر وضعیت بزنید تا اعلان تلگرامی آن روشن/خاموش شود "
        "(این تنظیم هم پیام خصوصی و هم کانال را کنترل می‌کند):",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("st:notif:toggle:"))
async def st_notif_toggle(callback: CallbackQuery):
    key = callback.data.split(":", 3)[3]
    valid_keys = {k for k, _ in NOTIF_STATUSES}
    if key not in valid_keys:
        await callback.answer("کلید نامعتبر", show_alert=True)
        return
    new_val = await settings_service.toggle(f"notif_enabled_{key}")
    await callback.answer("روشن شد ✅" if new_val else "خاموش شد ⛔️")
    kb = await _notif_kb()
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass


# ==========================================
# ۳. اتصال کانال اعلان‌ها
# ==========================================
async def _channel_menu_text_and_kb():
    channel_id = await settings_service.get("channel_id")
    mode = await settings_service.get("channel_mode")
    scope = await settings_service.get("channel_scope")

    status_line = f"📡 وضعیت: متصل به <code>{channel_id}</code>" if channel_id else "📡 وضعیت: هیچ کانالی متصل نیست"
    mode_label = "فقط کانال" if mode == "channel_only" else "هم کانال هم پیوی"
    scope_label = "فقط سفارش جدید" if scope == "new_only" else "همه تغییرات وضعیت"

    text = (
        "📢 <b>اتصال کانال اعلان‌ها</b>\n\n"
        f"{status_line}\n"
        f"🔀 حالت ارسال: {mode_label}\n"
        f"🎯 دامنه ارسال: {scope_label}\n\n"
        "برای تنظیم، دکمه «تنظیم/تغییر کانال» را بزنید و یک پیام از کانال (که ربات در آن ادمین است) "
        "فوروارد کنید، یا آیدی عددی کانال (مثل -100XXXXXXXXXX) را بفرستید."
    )
    rows = []
    if channel_id:
        rows.append([InlineKeyboardButton(text="🔀 تغییر حالت ارسال", callback_data="st:channel:mode")])
        rows.append([InlineKeyboardButton(text="🎯 تغییر دامنه ارسال", callback_data="st:channel:scope")])
        rows.append([InlineKeyboardButton(text="📡 ارسال پیام تست", callback_data="st:channel:test")])
        rows.append([InlineKeyboardButton(text="🔌 قطع اتصال کانال", callback_data="st:channel:disconnect")])
    rows.append([InlineKeyboardButton(text="✏️ تنظیم/تغییر کانال", callback_data="st:channel:set")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات", callback_data="st:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:channel")
async def st_channel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    text, kb = await _channel_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:channel:set")
async def st_channel_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsFSM.waiting_channel)
    await _safe_edit(
        callback.message,
        "📥 یک پیام از کانال موردنظر فوروارد کنید، یا آیدی عددی کانال را بفرستید.\nبرای انصراف /cancel بزنید.",
        reply_markup=_back_kb()
    )


@router.message(SettingsFSM.waiting_channel)
async def st_channel_receive(message: Message, state: FSMContext, bot: Bot):
    channel_id = None
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        channel_id = message.forward_from_chat.id
    elif message.text:
        txt = message.text.strip()
        if txt == "/cancel":
            await state.clear()
            await message.answer("❌ لغو شد.")
            return
        try:
            channel_id = int(txt)
        except ValueError:
            await message.answer("⚠️ فرمت نامعتبر است. پیام کانال را فوروارد کنید یا آیدی عددی بفرستید.")
            return
    else:
        await message.answer("⚠️ لطفاً پیام کانال را فوروارد کنید یا آیدی عددی بفرستید.")
        return

    wait = await message.answer("⏳ در حال تست ارسال پیام به کانال...")
    try:
        await bot.send_message(chat_id=channel_id, text="✅ این پیام تستی است. اتصال با موفقیت برقرار شد.")
    except Exception as e:
        await wait.edit_text(
            f"❌ نتوانستم پیام تست بفرستم.\n<code>{str(e)[:200]}</code>\n\n"
            f"مطمئن شوید ربات <b>ادمین</b> کانال است و دوباره تلاش کنید.",
            parse_mode="HTML"
        )
        await settings_service.log_error("channel_setup", str(e))
        return

    await settings_service.set("channel_id", str(channel_id))
    await state.clear()
    await wait.edit_text("✅ کانال با موفقیت متصل شد!")
    text, kb = await _channel_menu_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:channel:disconnect")
async def st_channel_disconnect(callback: CallbackQuery):
    await settings_service.set("channel_id", "")
    await callback.answer("کانال قطع شد.", show_alert=True)
    text, kb = await _channel_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:channel:mode")
async def st_channel_mode(callback: CallbackQuery):
    current = await settings_service.get("channel_mode")
    new_val = "channel_only" if current != "channel_only" else "both"
    await settings_service.set("channel_mode", new_val)
    await callback.answer("حالت ارسال تغییر کرد.")
    text, kb = await _channel_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:channel:scope")
async def st_channel_scope(callback: CallbackQuery):
    current = await settings_service.get("channel_scope")
    new_val = "new_only" if current != "new_only" else "all_status"
    await settings_service.set("channel_scope", new_val)
    await callback.answer("دامنه ارسال تغییر کرد.")
    text, kb = await _channel_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:channel:test")
async def st_channel_test(callback: CallbackQuery, bot: Bot):
    channel_id = await settings_service.get("channel_id")
    if not channel_id:
        await callback.answer("کانالی متصل نیست.", show_alert=True)
        return
    try:
        await bot.send_message(chat_id=int(channel_id), text="📡 پیام تست از پنل تنظیمات ربات.")
        await callback.answer("پیام تست ارسال شد ✅", show_alert=True)
    except Exception as e:
        await settings_service.log_error("channel_test", str(e))
        await callback.answer(f"❌ خطا: {str(e)[:150]}", show_alert=True)


# ==========================================
# ۵. آمار کلی فروشگاه
# ==========================================
def _order_is_today(order: dict, today_date) -> bool:
    date_str = order.get("date_created") or order.get("date_created_gmt")
    if not date_str:
        return False
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        return d == today_date
    except Exception:
        return False


async def _build_stats_text() -> str:
    lines = ["📊 <b>آمار کلی فروشگاه</b>\n"]
    try:
        orders = await wc_service.get_recent_orders(per_page=100)
        today_date = datetime.now().date()
        today_orders = [o for o in (orders or []) if _order_is_today(o, today_date)]
        total_amount = sum(float(o.get("total") or 0) for o in today_orders)
        lines.append(f"📦 سفارش امروز: <b>{len(today_orders)}</b> عدد")
        lines.append(f"💰 فروش امروز: <b>{total_amount:,.0f}</b> تومان")
    except Exception as e:
        lines.append("📦 سفارش امروز: ❌ خطا در دریافت")
        await settings_service.log_error("stats_orders", str(e))

    try:
        pool = await db_service.get_pool()
        async with pool.acquire() as conn:
            product_count = await conn.fetchval("SELECT COUNT(*) FROM products")
            keyword_count = await conn.fetchval("SELECT COUNT(*) FROM seo_ledger")
        lines.append(f"🛍 محصولات بایگانی‌شده: <b>{product_count}</b>")
        lines.append(f"📝 کلمات کلیدی مقالات: <b>{keyword_count}</b>")
    except Exception as e:
        lines.append("🛍 محصولات/مقالات: ❌ خطا در دریافت")
        await settings_service.log_error("stats_db", str(e))

    return "\n".join(lines)


@router.callback_query(F.data == "st:stats")
async def st_stats(callback: CallbackQuery):
    await callback.answer()
    await _safe_edit(callback.message, "⏳ در حال جمع‌آوری آمار...")
    text = await _build_stats_text()
    await _safe_edit(callback.message, text, reply_markup=_back_kb(), parse_mode="HTML")


# ==========================================
# ۶. زمان‌بندی خودکار Sync
# ==========================================
async def _sync_menu_text_and_kb():
    enabled = await settings_service.get("sync_auto_enabled")
    hour = await settings_service.get("sync_auto_hour")
    last_p_at = await settings_service.get("sync_last_products_at")
    last_p_n = await settings_service.get("sync_last_products_count")
    last_a_at = await settings_service.get("sync_last_articles_at")
    last_a_n = await settings_service.get("sync_last_articles_count")

    status_icon = "🟢 فعال" if enabled else "🔴 غیرفعال"
    text = (
        "⏰ <b>زمان‌بندی خودکار Sync</b>\n\n"
        f"وضعیت: {status_icon}\n"
        f"ساعت اجرا: <b>{hour}:00</b>\n\n"
        f"📦 آخرین سینک محصولات: {last_p_at or 'هنوز اجرا نشده'} ({last_p_n} عدد)\n"
        f"📝 آخرین سینک مقالات: {last_a_at or 'هنوز اجرا نشده'} ({last_a_n} عدد)\n\n"
        "ربات هر روز راس ساعت تنظیم‌شده، خودش محصولات و مقالات را سینک می‌کند."
    )
    rows = [
        [InlineKeyboardButton(text=("🔴 غیرفعال‌کردن" if enabled else "🟢 فعال‌کردن"), callback_data="st:sync:toggle")],
        [
            InlineKeyboardButton(text="➖ ساعت", callback_data="st:sync:hour:dec"),
            InlineKeyboardButton(text="➕ ساعت", callback_data="st:sync:hour:inc"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت به تنظیمات", callback_data="st:menu")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:sync")
async def st_sync(callback: CallbackQuery):
    await callback.answer()
    text, kb = await _sync_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:sync:toggle")
async def st_sync_toggle(callback: CallbackQuery):
    await settings_service.toggle("sync_auto_enabled")
    await callback.answer()
    text, kb = await _sync_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.in_({"st:sync:hour:inc", "st:sync:hour:dec"}))
async def st_sync_hour(callback: CallbackQuery):
    hour = await settings_service.get("sync_auto_hour")
    delta = 1 if callback.data.endswith("inc") else -1
    new_hour = (int(hour) + delta) % 24
    await settings_service.set("sync_auto_hour", new_hour)
    await callback.answer(f"ساعت روی {new_hour}:00 تنظیم شد")
    text, kb = await _sync_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


# ==========================================
# ۹. آخرین خطاها
# ==========================================
@router.callback_query(F.data == "st:errors")
async def st_errors(callback: CallbackQuery):
    await callback.answer()
    rows = await settings_service.get_recent_errors(10)
    if not rows:
        text = "🚨 <b>آخرین خطاها</b>\n\nخطایی ثبت نشده است. ✅"
    else:
        lines = ["🚨 <b>آخرین خطاهای ثبت‌شده</b>\n"]
        for r in rows:
            ts = r["created_at"].strftime("%m-%d %H:%M")
            src = r["source"]
            msg = (r["message"] or "")[:120]
            lines.append(f"▪️ <code>{ts}</code> [{src}]: {msg}")
        text = "\n".join(lines)
    await _safe_edit(callback.message, text, reply_markup=_back_kb(), parse_mode="HTML")


# ==========================================
# ۱۱. مدیریت دسترسی (ادمین‌های کمکی)
# ==========================================
async def _admins_menu_text_and_kb():
    admins = await settings_service.list_extra_admins()
    lines = [
        "👥 <b>مدیریت دسترسی</b>\n",
        f"👑 ادمین اصلی: <code>{ADMIN_ID}</code> (ثابت)\n",
    ]
    rows = []
    if admins:
        lines.append("ادمین‌های کمکی:")
        for uid in sorted(admins):
            lines.append(f"▪️ <code>{uid}</code>")
            rows.append([InlineKeyboardButton(text=f"❌ حذف {uid}", callback_data=f"st:admins:remove:{uid}")])
    else:
        lines.append("هیچ ادمین کمکی‌ای اضافه نشده است.")
    rows.append([InlineKeyboardButton(text="➕ افزودن ادمین جدید", callback_data="st:admins:add")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات", callback_data="st:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:admins")
async def st_admins(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    text, kb = await _admins_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "st:admins:add")
async def st_admins_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsFSM.waiting_admin_id)
    await _safe_edit(
        callback.message,
        "📥 آیدی عددی تلگرام فرد موردنظر را بفرستید (مثلاً از @userinfobot بگیرید)، یا پیامی از او فوروارد کنید.\n"
        "برای انصراف /cancel بزنید.",
        reply_markup=_back_kb()
    )


@router.message(SettingsFSM.waiting_admin_id)
async def st_admins_receive(message: Message, state: FSMContext):
    new_id = None
    if message.forward_from:
        new_id = message.forward_from.id
    elif message.text:
        txt = message.text.strip()
        if txt == "/cancel":
            await state.clear()
            await message.answer("❌ لغو شد.")
            return
        try:
            new_id = int(txt)
        except ValueError:
            await message.answer("⚠️ آیدی نامعتبر است. یک عدد بفرستید یا پیام فرد را فوروارد کنید.")
            return
    else:
        await message.answer("⚠️ لطفاً آیدی عددی بفرستید یا پیام فرد را فوروارد کنید.")
        return

    if new_id == ADMIN_ID:
        await message.answer("این کاربر همین حالا هم ادمین اصلی است.")
        await state.clear()
        return

    await settings_service.add_admin(new_id, added_by=message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ کاربر <code>{new_id}</code> به‌عنوان ادمین کمکی اضافه شد.\n"
        f"⚠️ توجه: این فرد به همه‌ی بخش‌های ربات دسترسی کامل خواهد داشت، "
        f"<b>به‌جز دکمه «📦 آخرین سفارش‌ها»</b> که به‌صورت جداگانه فقط مخصوص ادمین اصلی نوشته شده.",
        parse_mode="HTML"
    )
    text, kb = await _admins_menu_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("st:admins:remove:"))
async def st_admins_remove(callback: CallbackQuery):
    uid_str = callback.data.split(":", 3)[3]
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer("خطا", show_alert=True)
        return
    await settings_service.remove_admin(uid)
    await callback.answer("حذف شد ✅")
    text, kb = await _admins_menu_text_and_kb()
    await _safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
