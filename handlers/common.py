from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

# ایمپورت سرویس ووکامرس از پوشه جدید سرویس‌ها
from services.woocommerce import wc_service_instance as wc_service

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    # طراحی دکمه‌های کیبورد پایین صفحه با چیدمان متقارن (۸ دکمه)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤖 محصول با Gemini"), KeyboardButton(text="⚡ افزودن خودکار (AI)")],
            [KeyboardButton(text="➕ محصول جدید"), KeyboardButton(text="🛍 محصولات سایت")],
            [KeyboardButton(text="📝 مقاله جدید"), KeyboardButton(text="✏️ ویرایش مقاله")],
            [KeyboardButton(text="📦 آخرین سفارش‌ها"), KeyboardButton(text="⚙️ تنظیمات")]
        ],
        resize_keyboard=True,
        input_field_placeholder="یک گزینه را انتخاب کنید..."
    )
    
    await message.answer(
        "به پنل مدیریت یکپارچه فروشگاه خوش آمدید! 🏺\n\n"
        "سیستم امنیتی فعال است. لطفاً برای شروع از منوی زیر یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=keyboard
    )

@router.message(F.text.contains("محصولات سایت"))
async def test_get_products(message: Message):
    wait_msg = await message.answer("⏳ در حال ارتباط با سایت و دریافت محصولات...")
    
    try:
        # دریافت ۳ محصول آخر از سایت
        products = await wc_service.get_latest_products(per_page=3)
        
        if not products:
            await wait_msg.edit_text("محصولی یافت نشد.")
            return
            
        text = "🛍 ۳ محصول آخر سایت شما:\n\n"
        for p in products:
            price = p.get('price', 'نامشخص')
            text += f"▪️ {p['name']} - {price} تومان\n"
            
        await wait_msg.edit_text(text)
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در اتصال به سایت:\n{str(e)[:500]}")
