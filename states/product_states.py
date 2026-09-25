from aiogram.fsm.state import State, StatesGroup

class ProductWizard(StatesGroup):
    waiting_for_name = State()
    waiting_for_ai_text = State()  
    
    waiting_for_price = State()
    waiting_for_stock = State()
    
    waiting_for_short_desc = State()
    waiting_for_long_desc = State()
    
    waiting_for_image = State()
    waiting_for_image_alt = State()
    waiting_for_image_title = State()
    
    # وضعیت‌های کامل سئو (شامل پیوند دایمی / Slug)
    waiting_for_seo_keyword = State()
    waiting_for_seo_slug = State()
    waiting_for_seo_title = State()
    waiting_for_seo_desc = State()
    
    # وضعیت‌های جدید برای گالری تصاویر
    waiting_for_gallery_image = State()
    waiting_for_gallery_alt = State()
    waiting_for_gallery_title = State()
