import asyncio
import os
import json
import hmac
import hashlib
import base64
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# وارد کردن متغیرهای محیطی
from config import BOT_TOKEN, ADMIN_ID, WC_WEBHOOK_SECRET

# وارد کردن میدل‌ورهای امنیتی و ضد-هنگ
from utils.security import AdminOnlyMiddleware, ClearStateOnMenuMiddleware

# وارد کردن روترها (به ترتیب اهمیت)
from handlers.common import router as common_router
from handlers.gemini_products import router as gemini_products_router
from handlers.gemini_articles import router as gemini_articles_router
from handlers.products import router as products_router
from handlers.orders import router as orders_router
from handlers.admin_sync import router as admin_sync_router  # 🌟 اضافه شدن روتر همگام‌سازی

# وارد کردن سرویس‌های ارتباطی ایزوله
from services.database import db_service
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service

# ساخت یک روتر داخلی فقط برای دکمه‌های مربوط به وب‌هوک سفارشات
webhook_router = Router()

# ==========================================
# 🌟 ماشین‌حالت اعلان سفارش: تشخیص گروه معنادار وضعیت پرداخت
# (فقط برای تشخیص «تغییر معنادار»؛ متن پیام همچنان بر اساس status خام ساخته می‌شود)
# ==========================================
PAID_STATUSES = {"processing", "completed"}
PENDING_STATUSES = {"pending", "on-hold"}

def _payment_state(status: str) -> str:
    if status in PAID_STATUSES:
        return "paid"
    if status in PENDING_STATUSES:
        return "pending"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "failed"
    # وضعیت ناشناخته: گروه مستقل خودش (رفتار محافظه‌کارانه، هر تغییری معنادار تلقی می‌شود)
    return status

# ==========================================
# دکمه: سفارش رو گرفتم ✅ (پایدار در دیتابیس + ضد-تکرار)
# ==========================================
@webhook_router.callback_query(F.data.startswith("ack_order_"))
async def ack_order_callback(callback: CallbackQuery):
    order_id = callback.data.split("_")[2]

    db_pool = await db_service.get_pool()
    async with db_pool.acquire() as conn:
        # آپدیت شرطی: فقط اگر قبلاً تایید نشده باشد اعمال می‌شود -> جلوگیری از پردازش تکراری/رقابتی
        result = await conn.execute(
            "UPDATE order_notifications SET admin_confirmed = TRUE WHERE order_id = $1 AND admin_confirmed = FALSE",
            order_id
        )

    # result مثل "UPDATE 0" یا "UPDATE 1" است
    rows_affected = int(result.split()[-1]) if result else 0

    if rows_affected == 0:
        # یا قبلاً تایید شده، یا رکوردی برای این سفارش وجود ندارد (پیام قدیمی/یتیم)
        await callback.answer("این سفارش قبلاً تایید و بسته شده است.", show_alert=True)
        return

    new_text = callback.message.html_text + "\n\n✅ <b>توسط ادمین تایید و دریافت شد! (بسته‌بندی)</b>"
    await callback.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
    await callback.answer(f"سفارش #{order_id} بسته شد!", show_alert=True)

# صفحه ساده برای بررسی سلامت سرور وب (Render Health Check)
async def health_check(request):
    return web.Response(text="🏺 CitySofal Bot is Live and Modular!")

# 🌟 سقف زمانی قطعی برای کل عملیات دیتابیس+ارسال تلگرام یک وب‌هوک
# دلیل وجودش: اگر کانکشن دیتابیس به‌صورت خاموش (TCP مرده، بدون RST/FIN) گیر کند،
# بدون این سقف کل تسک aiohttp برای همیشه معلق می‌ماند، هیچ Exception ای رخ نمی‌دهد
# و هیچ پیامی (نه موفق، نه خطا) هرگز ارسال نمی‌شود -> سکوت کامل و دائمی.
WEBHOOK_PROCESSING_TIMEOUT = 45

# ==========================================
# پردازش اصلی رویداد سفارش (منطق ماشین‌حالت) - جدا شده تا بتوان با wait_for محافظتش کرد
# ==========================================
async def _process_order_event(bot_instance, db_pool, order_id, status, data):
    # ==========================================
    # 🌟 ماشین‌حالت اعلان سفارش
    # - لاک تراکنشی per-order (pg_advisory_xact_lock): وب‌هوک‌های همزمان همین سفارش صف می‌شوند
    #   و از رکوردهای رقابتی/پیام تکراری جلوگیری می‌کند (بخش ۱۷ سند معماری).
    # - تصمیم بر اساس گروه منطقی وضعیت پرداخت گرفته می‌شود، نه رشته خام status
    #   (مثلاً processing -> completed پیام تکراری نمی‌سازد، چون هر دو گروه "paid" هستند).
    # ==========================================
    new_payment_state = _payment_state(status)

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1)::bigint)", order_id)

            row = await conn.fetchrow(
                'SELECT status, message_id, payment_state, admin_confirmed FROM order_notifications WHERE order_id = $1',
                order_id
            )

            is_new = row is None
            old_message_id = row['message_id'] if row else None
            old_payment_state = row['payment_state'] if row else None

            # رکوردهای قدیمی از قبل از مهاجرت payment_state=NULL دارند؛ یک‌بار همگام‌سازی می‌شوند
            state_changed = is_new or old_payment_state is None or old_payment_state != new_payment_state

            if not state_changed:
                return web.json_response({"status": "ignored_duplicate"}, status=200)

            if old_message_id and not is_new:
                try:
                    await bot_instance.delete_message(chat_id=ADMIN_ID, message_id=old_message_id)
                except Exception:
                    pass

            # استخراج اطلاعات سفارش
            total = str(data.get("total", "0"))
            payment_method_title = data.get("payment_method_title", "نامشخص")
            customer_note = data.get("customer_note", "")
            billing = data.get("billing", {})
            first_name = billing.get("first_name", "ثبت‌نشده")
            last_name = billing.get("last_name", "")
            phone = billing.get("phone", "ثبت‌نشده")
            city = billing.get("city", "")
            address_1 = billing.get("address_1", "")
            state = billing.get("state", "")

            shipping_lines = data.get("shipping_lines", [])
            shipping_method = shipping_lines[0].get("method_title", "پست/تیپاکس") if shipping_lines else "پیش‌فرض"

            line_items = data.get("line_items", [])
            products_list = ""
            for index, item in enumerate(line_items, 1):
                p_name = item.get("name", "محصول")
                p_qty = str(item.get("quantity", 1))
                p_total = str(item.get("total", "0"))
                products_list += f"{index}. {p_name}\n   - تعداد: {p_qty} | مبلغ: {p_total} تومان\n"

            status_translations = {
                "pending": "⏳ در انتظار پرداخت (ثبت اولیه)",
                "processing": "💳✅ پرداخت موفق و قطعی",
                "on-hold": "⏸ در انتظار بررسی",
                "completed": "🎉 تکمیل‌شده و ارسال شده",
                "cancelled": "❌ لغو شده",
                "failed": "⚠️ پرداخت ناموفق"
            }
            persian_status = status_translations.get(status, status)

            if status in ["processing", "completed"]:
                header_title = "💰 گزارش واریز وجه و ثبت سفارش قطعی در شهر سفال!"
            elif status == "failed":
                header_title = "⚠️ هشدار: تلاش ناموفق برای پرداخت در سایت!"
            else:
                header_title = "🔔 ثبت سفارش جدید (در انتظار پرداخت):"

            order_text = (
                f"<b>{header_title}</b>\n\n"
                f"🆔 شماره سفارش: #{order_id}\n"
                f"📌 وضعیت: {persian_status}\n"
                f"💳 پرداخت: {payment_method_title}\n"
                f"🚚 ارسال: {shipping_method}\n\n"
                f"👤 مشتری: {first_name} {last_name}\n"
                f"📞 تلفن: <code>{phone}</code>\n"
                f"📍 آدرس: {state}، {city}، {address_1}\n\n"
                f"🛒 اقلام:\n{products_list}\n"
                f"💰 کل (با هزینه ارسال): {total} تومان"
            )
            if customer_note:
                order_text += f"\n\n📝 یادداشت: {customer_note}"

            reply_markup = None
            if status in ["processing", "completed"]:
                reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📦 سفارش رو گرفتم (بستن)", callback_data=f"ack_order_{order_id}")]
                ])

            sent_msg = await bot_instance.send_message(
                chat_id=ADMIN_ID,
                text=order_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )

            # 🌟 نوشتن اتمیک وضعیت جدید + بازنشانی admin_confirmed چون پیام/گروه عوض شده
            await conn.execute('''
                INSERT INTO order_notifications (order_id, status, payment_state, message_id, admin_confirmed)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (order_id) DO UPDATE
                SET status = $2, payment_state = $3, message_id = $4, admin_confirmed = FALSE
            ''', order_id, status, new_payment_state, sent_msg.message_id)

    return web.json_response({"status": "success", "order_id": order_id}, status=200)

# ==========================================
# دریافت و بررسی وب‌هوک سفارش از ووکامرس
# ==========================================
async def handle_order_webhook(request):
    bot_instance = request.app['bot']
    db_pool = await db_service.get_pool()
    
    try:
        body = await request.text()
        if not body:
            return web.json_response({"status": "ignored", "message": "Empty body"}, status=200)

        # لایه امنیتی HMAC
        received_signature = request.headers.get("x-wc-webhook-signature")
        if not received_signature:
            return web.json_response({"status": "unauthorized"}, status=401)

        expected_signature = base64.b64encode(
            hmac.new(WC_WEBHOOK_SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')

        if not hmac.compare_digest(received_signature, expected_signature):
            try:
                await asyncio.wait_for(
                    bot_instance.send_message(
                        chat_id=ADMIN_ID,
                        text="⚠️ <b>هشدار امنیتی:</b> تلاش مسدود شد! ریکوئست فیک به وب‌هوک ارسال گردید.",
                        parse_mode="HTML"
                    ),
                    timeout=10
                )
            except Exception:
                pass
            return web.json_response({"status": "unauthorized"}, status=401)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return web.json_response({"status": "received_non_json"}, status=200)

        order_id = str(data.get("id", "نامشخص"))
        status = data.get("status", "نامشخص")
        print(f"📥 وب‌هوک سفارش دریافت شد: #{order_id} | status={status}")

        # 🌟 سقف زمانی قطعی: هرچه پیش بیاید (کانکشن مرده دیتابیس، تلگرام کند و ...)
        # این درخواست حداکثر WEBHOOK_PROCESSING_TIMEOUT ثانیه معلق می‌ماند، نه برای همیشه.
        try:
            return await asyncio.wait_for(
                _process_order_event(bot_instance, db_pool, order_id, status, data),
                timeout=WEBHOOK_PROCESSING_TIMEOUT
            )
        except asyncio.TimeoutError:
            print(f"⏱ Timeout: پردازش سفارش #{order_id} بعد از {WEBHOOK_PROCESSING_TIMEOUT} ثانیه لغو شد (احتمالاً کانکشن دیتابیس/تلگرام معلق مانده بود)")
            try:
                await asyncio.wait_for(
                    bot_instance.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"⏱ <b>Timeout در پردازش سفارش #{order_id}</b>\n"
                            f"عملیات بعد از {WEBHOOK_PROCESSING_TIMEOUT} ثانیه لغو شد (احتمالاً کانکشن دیتابیس/تلگرام معلق مانده). "
                            f"لطفاً وضعیت این سفارش را دستی در سایت بررسی کنید."
                        ),
                        parse_mode="HTML"
                    ),
                    timeout=10
                )
            except Exception:
                pass
            return web.json_response({"status": "timeout", "order_id": order_id}, status=200)

    except Exception as e:
        print(f"🐞 خطای مخفی در پردازش وب‌هوک: {e}")
        # 🌟 چاپ خطای خاموش در تلگرام ادمین برای عیب‌یابی سریع (با سقف زمانی، تا خودش هنگ نکند)
        try:
            await asyncio.wait_for(
                bot_instance.send_message(
                    chat_id=ADMIN_ID, 
                    text=f"🐞 <b>خطای مخفی در پردازش وب‌هوک:</b>\n<code>{str(e)}</code>", 
                    parse_mode="HTML"
                ),
                timeout=10
            )
        except Exception:
            pass
        return web.json_response({"status": "error", "message": str(e)}, status=200)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    print("🔌 Initializing Database Pool...")
    await db_service.get_pool()
    print("✅ Database ready!")

    # ثبت میدل‌ورهای سراسری
    dp.message.middleware(AdminOnlyMiddleware())
    dp.callback_query.middleware(AdminOnlyMiddleware())
    dp.message.middleware(ClearStateOnMenuMiddleware())

    # ثبت روترها (ترتیب در Aiogram مهم است)
    dp.include_router(common_router)
    dp.include_router(gemini_products_router)   # پردازش محصولات هوشمند
    dp.include_router(gemini_articles_router)   # پردازش مقالات هوشمند
    dp.include_router(products_router)          # پردازش محصولات دستی
    dp.include_router(orders_router)            # پردازش سفارشات
    dp.include_router(admin_sync_router)        # 🌟 پردازش همگام‌سازی دیتابیس
    dp.include_router(webhook_router)           # پردازش دکمه‌های وب‌هوک

    app = web.Application()
    app['bot'] = bot
    
    app.router.add_get('/', health_check)
    app.router.add_post('/webhook/order', handle_order_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"🌐 Web server started on port {port}")
    print("🚀 Bot is running with Modular Architecture & Gemini Integration...")
    
    try:
        await dp.start_polling(bot)
    finally:
        print("🛑 Shutting down smoothly...")
        await bot.session.close()
        await runner.cleanup()
        await db_service.close()
        await wc_service.close()
        await wp_service.close()

if __name__ == "__main__":
    asyncio.run(main())
