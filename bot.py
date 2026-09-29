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
from handlers.admin_sync import router as admin_sync_router
from handlers.settings import router as settings_router

# وارد کردن سرویس‌های ارتباطی ایزوله
from services.database import db_service
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service
from services.settings_service import settings_service
from services.scheduler_service import scheduler_loop

webhook_router = Router()

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
    return status

IRAN_STATE_NAMES = {
    "KHZ": "خوزستان", "THR": "تهران", "ILM": "ایلام", "BHR": "بوشهر",
    "ADL": "اردبیل", "ESF": "اصفهان", "YZD": "یزد", "KRH": "کرمانشاه",
    "KRN": "کرمان", "HDN": "همدان", "GZN": "قزوین", "ZJN": "زنجان",
    "LRS": "لرستان", "ABZ": "البرز", "EAZ": "آذربایجان شرقی", "WAZ": "آذربایجان غربی",
    "CHB": "چهارمحال و بختیاری", "SKH": "خراسان جنوبی", "RKH": "خراسان رضوی", "NKH": "خراسان شمالی",
    "SMN": "سمنان", "FRS": "فارس", "QHM": "قم", "KRD": "کردستان",
    "KBD": "کهگیلویه و بویراحمد", "GLS": "گلستان", "GIL": "گیلان", "MZN": "مازندران",
    "MKZ": "مرکزی", "HRZ": "هرمزگان", "SBN": "سیستان و بلوچستان",
}

def _state_name(code: str) -> str:
    return IRAN_STATE_NAMES.get(code, code)

@webhook_router.callback_query(F.data.startswith("ack_order_"))
async def ack_order_callback(callback: CallbackQuery):
    order_id = callback.data.split("_")[2]

    db_pool = await db_service.get_pool()
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE order_notifications SET admin_confirmed = TRUE WHERE order_id = $1 AND admin_confirmed = FALSE",
            order_id
        )

    rows_affected = int(result.split()[-1]) if result else 0

    if rows_affected == 0:
        await callback.answer("این سفارش قبلاً تایید و بسته شده است.", show_alert=True)
        return

    new_text = callback.message.html_text + "\n\n✅ <b>توسط ادمین تایید و دریافت شد! (بسته‌بندی)</b>"
    await callback.message.edit_text(text=new_text, parse_mode="HTML", reply_markup=None)
    await callback.answer(f"سفارش #{order_id} بسته شد!", show_alert=True)

async def health_check(request):
    return web.Response(text="🏺 CitySofal Bot is Live and Modular!")

WEBHOOK_PROCESSING_TIMEOUT = 45

async def _process_order_event(bot_instance, db_pool, order_id, status, data):
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

            state_changed = is_new or old_payment_state is None or old_payment_state != new_payment_state

            if not state_changed:
                return web.json_response({"status": "ignored_duplicate"}, status=200)

            if old_message_id and not is_new:
                try:
                    await bot_instance.delete_message(chat_id=ADMIN_ID, message_id=old_message_id)
                except Exception:
                    pass

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
            postcode = billing.get("postcode", "")

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
                f"📍 آدرس: {_state_name(state)}، {city}، {address_1}\n"
                f"📮 کد پستی: <code>{postcode}</code>\n\n"
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

            notif_key = "on_hold" if status == "on-hold" else status
            try:
                notif_enabled = await settings_service.get(f"notif_enabled_{notif_key}")
                if notif_enabled is None:
                    notif_enabled = True
            except Exception:
                notif_enabled = True

            sent_msg = None
            if notif_enabled:
                channel_id, channel_mode, channel_scope = None, "both", "all_status"
                try:
                    channel_id = await settings_service.get("channel_id")
                    channel_mode = await settings_service.get("channel_mode") or "both"
                    channel_scope = await settings_service.get("channel_scope") or "all_status"
                except Exception:
                    pass

                send_to_private = not (channel_id and channel_mode == "channel_only")
                if send_to_private:
                    sent_msg = await bot_instance.send_message(
                        chat_id=ADMIN_ID,
                        text=order_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )

                if channel_id:
                    scope_ok = (channel_scope != "new_only") or (status == "pending" and is_new)
                    if scope_ok:
                        try:
                            await bot_instance.send_message(
                                chat_id=int(channel_id),
                                text=order_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                        except Exception as ch_err:
                            await settings_service.log_error("channel_notify", str(ch_err))

            message_id_to_store = sent_msg.message_id if sent_msg else None

            await conn.execute('''
                INSERT INTO order_notifications (order_id, status, payment_state, message_id, admin_confirmed)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (order_id) DO UPDATE
                SET status = $2, payment_state = $3, message_id = $4, admin_confirmed = FALSE
            ''', order_id, status, new_payment_state, message_id_to_store)

    return web.json_response({"status": "success", "order_id": order_id}, status=200)

# ==========================================
# 🌟 سیستم جدید Direct API: مچ شده با کد PHP سایت
# ==========================================
async def handle_direct_order(request):
    bot_instance = request.app['bot']
    db_pool = await db_service.get_pool()
    
    try:
        body = await request.json()
        
        # بررسی رمز امنیتی با رمزی که در سایت قرار دادید
        if body.get("secret") != WC_WEBHOOK_SECRET:
            print("⚠️ هشدار امنیتی: رمز اتصال مستقیم اشتباه است.")
            return web.json_response({"status": "unauthorized"}, status=401)

        data = body.get("data", {})
        order_id = str(data.get("id", "نامشخص"))
        status = data.get("status", "نامشخص")
        
        print(f"🚀 دریافت موشکی سفارش (Direct API): #{order_id} | status={status}")

        try:
            return await asyncio.wait_for(
                _process_order_event(bot_instance, db_pool, order_id, status, data),
                timeout=WEBHOOK_PROCESSING_TIMEOUT
            )
        except asyncio.TimeoutError:
            return web.json_response({"status": "timeout"}, status=200)

    except Exception as e:
        print(f"🐞 خطای سیستم مستقیم: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# ==========================================
# دریافت و بررسی وب‌هوک سفارش از ووکامرس (نسخه اصلاح شده قدیمی)
# ==========================================
async def handle_order_webhook(request):
    bot_instance = request.app['bot']
    db_pool = await db_service.get_pool()
    
    try:
        raw_body = await request.read()
        if not raw_body:
            return web.json_response({"status": "ignored", "message": "Empty body"}, status=200)

        body_text = raw_body.decode('utf-8')

        event = request.headers.get("x-wc-webhook-event", "")
        if event == "ping":
            print("🏓 Ping received and accepted from WooCommerce")
            return web.json_response({"status": "success", "message": "Ping accepted"}, status=200)

        received_signature = request.headers.get("x-wc-webhook-signature")
        if not received_signature:
            return web.json_response({"status": "unauthorized"}, status=401)

        expected_signature = base64.b64encode(
            hmac.new(WC_WEBHOOK_SECRET.encode('utf-8'), raw_body, hashlib.sha256).digest()
        ).decode('utf-8')

        if not hmac.compare_digest(received_signature, expected_signature):
            return web.json_response({"status": "unauthorized"}, status=401)

        try:
            data = json.loads(body_text)
        except json.JSONDecodeError:
            return web.json_response({"status": "received_non_json"}, status=200)

        order_id = str(data.get("id", "نامشخص"))
        status = data.get("status", "نامشخص")
        
        try:
            return await asyncio.wait_for(
                _process_order_event(bot_instance, db_pool, order_id, status, data),
                timeout=WEBHOOK_PROCESSING_TIMEOUT
            )
        except asyncio.TimeoutError:
            return web.json_response({"status": "timeout", "order_id": order_id}, status=200)

    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=200)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    print("🔌 Initializing Database Pool...")
    await db_service.get_pool()
    print("✅ Database ready!")

    dp.message.middleware(AdminOnlyMiddleware())
    dp.callback_query.middleware(AdminOnlyMiddleware())
    dp.message.middleware(ClearStateOnMenuMiddleware())

    dp.include_router(common_router)
    dp.include_router(gemini_products_router)
    dp.include_router(gemini_articles_router)
    dp.include_router(products_router)
    dp.include_router(orders_router)
    dp.include_router(admin_sync_router)
    dp.include_router(settings_router)
    dp.include_router(webhook_router)

    asyncio.create_task(scheduler_loop())

    app = web.Application()
    app['bot'] = bot
    
    app.router.add_get('/', health_check)
    app.router.add_post('/webhook/order', handle_order_webhook)
    # 🌟 مسیر کاملاً جدید که به قطعه کد سایت گوش می‌دهد
    app.router.add_post('/api/direct-order', handle_direct_order)
    
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
