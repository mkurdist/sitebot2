from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID

# استفاده از نشست یکتا و سراسری
from services.woocommerce import wc_service_instance as wc_service

router = Router()

# تابع کمکی برای ساخت متن و کیبورد لیست سفارش‌ها (جهت استفاده مجدد و رفع باگ کرش دکمه بازگشت)
async def generate_orders_list_data():
    try:
        orders = await wc_service.get_recent_orders(per_page=10)
    except Exception:
        return "❌ خطا در ارتباط با وب‌سایت برای دریافت سفارش‌ها.", None

    if not orders:
        return "📭 هیچ سفارشی در سایت ثبت نشده است.", None

    text = "📦 <b>۱۰ سفارش اخیر فروشگاه شهر سفال:</b>\nبرای مدیریت و تغییر وضعیت، روی سفارش مورد نظر کلیک کنید:"
    
    keyboard_builder = []
    status_emoji = {
        "pending": "⏳",
        "processing": "💳",
        "completed": "✅",
        "cancelled": "❌",
        "on-hold": "⏸"
    }

    for order in orders:
        o_id = order.get("id")
        total = order.get("total")
        status = order.get("status")
        billing = order.get("billing", {})
        name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        
        emoji = status_emoji.get(status, "📌")
        btn_text = f"{emoji} #{o_id} | {name} | {total} تومان"
        
        keyboard_builder.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_order_{o_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_builder)
    return text, markup


# ۱. نمایش لیست ۱۰ سفارش آخر
@router.message(F.text == "📦 آخرین سفارش‌ها")
async def show_recent_orders(message: Message):
    if message.from_user.id != int(ADMIN_ID):
        return

    text, markup = await generate_orders_list_data()
    # تغییر پارس‌مود به HTML برای جلوگیری از شکستن پیام‌ها
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


# ۲. نمایش جزئیات کامل یک سفارش و دکمه‌های تغییر وضعیت
@router.callback_query(F.data.startswith("view_order_"))
async def order_details_callback(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    
    try:
        order = await wc_service.get_order(order_id)
    except Exception:
        await callback.answer("❌ خطا در دریافت اطلاعات سفارش!", show_alert=True)
        return

    status = order.get("status")
    total = order.get("total", "0")
    payment_title = order.get("payment_method_title", "نامشخص")
    billing = order.get("billing", {})
    
    line_items = order.get("line_items", [])
    products_str = ""
    for item in line_items:
        products_str += f"▪️ {item.get('name')} (تعداد: {item.get('quantity')})\n"

    # استفاده از تگ‌های استاندارد HTML
    details_text = (
        f"🔍 <b>جزئیات سفارش #{order_id}</b>\n\n"
        f"👤 مشتری: {billing.get('first_name')} {billing.get('last_name')}\n"
        f"📞 تلفن: <code>{billing.get('phone')}</code>\n"
        f"📍 آدرس: {billing.get('city')} - {billing.get('address_1')}\n\n"
        f"🛒 اقلام:\n{products_str}\n"
        f"💰 مبلغ کل: <code>{total} تومان</code>\n"
        f"📌 وضعیت فعلی: <code>{status}</code>"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و تکمیل سفارش", callback_data=f"set_status_{order_id}_completed"),
        ],
        [
            InlineKeyboardButton(text="💳 در حال انجام (پرداخت شده)", callback_data=f"set_status_{order_id}_processing"),
            InlineKeyboardButton(text="❌ لغو سفارش", callback_data=f"set_status_{order_id}_cancelled")
        ],
        [
            InlineKeyboardButton(text="🔙 بازگشت به لیست سفارش‌ها", callback_data="back_to_orders_list")
        ]
    ])

    await callback.message.edit_text(text=details_text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


# ۳. اعمال تغییر وضعیت در ووکامرس
@router.callback_query(F.data.startswith("set_status_"))
async def change_order_status_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    order_id = int(parts[2])
    new_status = parts[3]

    try:
        await wc_service.update_order_status(order_id, new_status)
        status_fa = {
            "completed": "تکمیل‌شده ✅",
            "processing": "در حال انجام 💳",
            "cancelled": "لغو شده ❌"
        }.get(new_status, new_status)
        
        await callback.answer(f"وضعیت سفارش #{order_id} به '{status_fa}' تغییر یافت!", show_alert=True)
        await order_details_callback(callback)
    except Exception:
        await callback.answer("❌ خطا در بروزرسانی وضعیت سفارش در سایت!", show_alert=True)


# ۴. دکمه بازگشت به لیست (رفع باگ Crash)
@router.callback_query(F.data == "back_to_orders_list")
async def back_to_list_callback(callback: CallbackQuery):
    text, markup = await generate_orders_list_data()
    
    # بجای پاک کردن پیام، متن همان پیام را ویرایش می‌کنیم تا ارور ندهد
    if markup:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await callback.message.edit_text(text, parse_mode="HTML")
        
    await callback.answer()
