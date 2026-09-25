from aiogram.fsm.state import State, StatesGroup

class ArticleWizard(StatesGroup):
    waiting_for_article_text = State()
    waiting_for_featured_image = State()
    waiting_for_image_alt = State()
    waiting_for_image_title = State()
    waiting_for_categories = State()
    waiting_for_primary_category = State()
    waiting_for_publish_action = State()

    # وضعیت‌های ویرایش مقاله‌ی موجود
    waiting_for_edit_title = State()
    waiting_for_edit_content = State()
