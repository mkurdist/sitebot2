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

# وارد کردن سرویس‌های ارتباطی ایزوله
from services.database import db_service
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service

# ساخت یک روتر داخلی فقط برای دکمه‌های مربوط به وب‌هوک سفارشات
webhook_router = Router()

# ==========================================
# دکمه: سفارش رو گرفتم ✅
# ==========================================
@webhook_router.callback_query(F.data.startswith("ack_order_"))
async def ack_order_callback(callback: CallbackQuery):
    order_id = callback.data.split("_")[2]
    new_text = callback.message.html_text + "\n\n✅ <b>توسط ادمین تایید و دریافت شد! (بسته‌بندی)</b>"
    
    await callback.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
    await callback.answer(f"سفارش #{order_id} بسته شد!", show_alert=True)

# صفحه ساده برای بررسی سلامت سرور وب (Render Health Check)
async def health_check(request):
    return web.Response(text="🏺 CitySofal Bot is Live and Modular!")

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
            await bot_instance.send_message(
                chat_id=ADMIN_ID,
                text="⚠️ <b>هشدار امنیتی:</b> تلاش مسدود شد! ریکوئست فیک به وب‌هوک ارسال گردید.",
                parse_mode="HTML"
            )
            return web.json_response({"status": "unauthorized"}, status=401)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return web.json_response({"status": "received_non_json"}, status=200)

        order_id = str(data.get("id", "نامشخص"))
        status = data.get("status", "نامشخص")
        
        # ==========================================
        # منطق دیتابیس (جلوگیری از تکرار + پاک کردن پیام قبلی)
        # ==========================================
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT status, message_id FROM order_notifications WHERE order_id = $1', order_id)
            old_message_id = None
            
            if row:
                old_status = row['status']
                old_message_id = row['message_id']
                
                if old_status == status:
                    return web.json_response({"status": "ignored_duplicate"}, status=200)
                
                if old_message_id:
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
        
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO order_notifications (order_id, status, message_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (order_id) DO UPDATE 
                SET status = $2, message_id = $3
            ''', order_id, status, sent_msg.message_id)

        return web.json_response({"status": "success", "order_id": order_id}, status=200)
    
    except Exception as e:
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
