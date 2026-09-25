from aiogram.fsm.state import State, StatesGroup

# (کلاس قبلی - دست‌نخورده برای حفظ منطق‌های قدیمی در صورت نیاز)
class ArticleWizard(StatesGroup):
    waiting_for_article_text = State()
    waiting_for_featured_image = State()
    waiting_for_image_alt = State()
    waiting_for_image_title = State()
    waiting_for_categories = State()
    waiting_for_primary_category = State()
    waiting_for_publish_action = State()
    waiting_for_edit_title = State()
    waiting_for_edit_content = State()

# 🌟 (کلاس جدید) وضعیت‌های سیستم مقاله‌نویس هوشمند (ارتقا یافته)
class GeminiArticleWizard(StatesGroup):
    waiting_for_topic = State()             # مرحله ۱: دریافت موضوع از ادمین
    waiting_for_title_selection = State()   # مرحله ۲: انتظار برای کلیک روی یکی از عناوین پیشنهادی
    waiting_for_featured_image = State()    # مرحله ۳: دریافت عکس واقعی برای تصویر شاخص
    waiting_for_image_alt = State()         # (رزرو شده برای توسعه‌های آینده)
    waiting_for_image_title = State()       # (رزرو شده برای توسعه‌های آینده)
    waiting_for_category = State()          # 🌟 مرحله ۴ (جدید): انتظار برای انتخاب دسته‌بندی از لیست وردپرس
    waiting_for_publish_action = State()    # مرحله ۵: داشبورد انتشار نهایی
