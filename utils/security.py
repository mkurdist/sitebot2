from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from config import ADMIN_ID
from services.settings_service import settings_service  # 🌟 [ماژول تنظیمات] پشتیبانی ادمین‌های کمکی

class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)

        if user is None:
            return

        # مسیر اصلی و بدون تغییر: ادمین اصلی همیشه دسترسی کامل دارد
        if user.id == ADMIN_ID:
            return await handler(event, data)

        # 🌟 [ماژول تنظیمات] اگر ادمین کمکی از پنل تنظیمات اضافه شده باشد، او هم عبور می‌کند.
        # اگر سرویس تنظیمات هر دلیلی در دسترس نباشد، رفتار قبلی (فقط ADMIN_ID) حفظ می‌شود.
        try:
            extra_admins = await settings_service.list_extra_admins()
        except Exception:
            extra_admins = set()

        if user.id in extra_admins:
            return await handler(event, data)

        return

# لیست دکمه‌های منوی اصلی (به‌روزرسانی شده با دکمه جدید مقاله)
MENU_BUTTONS = {
    "🤖 محصول با Gemini", "⚡ افزودن خودکار (AI)", "➕ محصول جدید", 
    "🛍 محصولات سایت", "📝 مقاله با Gemini", "✏️ ویرایش مقاله", 
    "📦 آخرین سفارش‌ها", "⚙️ تنظیمات", "/start", "/cancel"
}

class ClearStateOnMenuMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if event.text and event.text in MENU_BUTTONS:
            state = data.get("state")
            if state:
                await state.clear()
        return await handler(event, data)
