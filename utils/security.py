from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from config import ADMIN_ID

class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        
        # اگر کاربر وجود نداشت یا آیدی او با ادمین یکی نبود، پیام را کاملاً نادیده بگیر
        if user is None or user.id != ADMIN_ID:
            return 
            
        return await handler(event, data)

# لیست دکمه‌های منوی اصلی
MENU_BUTTONS = {
    "🤖 محصول با Gemini", "⚡ افزودن خودکار (AI)", "➕ محصول جدید", 
    "🛍 محصولات سایت", "📝 مقاله جدید", "✏️ ویرایش مقاله", 
    "📦 آخرین سفارش‌ها", "⚙️ تنظیمات", "/start", "/cancel"
}

class ClearStateOnMenuMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if event.text and event.text in MENU_BUTTONS:
            state = data.get("state")
            if state:
                await state.clear()
        return await handler(event, data)
