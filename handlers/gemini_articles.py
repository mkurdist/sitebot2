import io
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from states.article_states import GeminiArticleWizard
from services.gemini_blog import generate_blog_titles, generate_blog_article
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service
from services.database import db_service  # 🌟 اتصال دیتابیس اختصاصی ربات

router = Router()

# ==========================================
# ۱. شروع پروسه و دریافت موضوع
# ==========================================
@router.message(F.text == "📝 مقاله با Gemini")
@router.message(F.text == "/gemini_blog")
async def start_gemini_blog(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(GeminiArticleWizard.waiting_for_topic)
    
    await message.answer(
        "🤖 <b>سیستم مقاله‌نویس هوشمند شهر سفال</b>\n\n"
        "لطفاً <b>موضوع کلی</b> یا <b>کلمه کلیدی</b> مقاله‌ای که می‌خواهید بنویسید را ارسال کنید.\n"
        "مثال: <i>چیدمان گلدان سفالی در آپارتمان کوچک</i>",
        parse_mode="HTML"
    )

# ==========================================
# ۲. دریافت موضوع، بررسی حافظه سئو و پیشنهاد عناوین
# ==========================================
@router.message(GeminiArticleWizard.waiting_for_topic)
async def process_topic_and_generate_titles(message: Message, state: FSMContext):
    topic = message.text.strip()
    wait_msg = await message.answer("⏳ در حال بررسی حافظه سئو و ایده‌پردازی عناوین...")
    
    try:
        # 🌟 واکشی کلمات کلیدی قفل‌شده از دیتابیس برای جلوگیری از همنوع‌خواری
        pool = await db_service.get_pool()
        locked_keywords = []
        async with pool.acquire() as conn:
            records = await conn.fetch('SELECT focus_keyword FROM seo_ledger WHERE focus_keyword IS NOT NULL')
            locked_keywords = [r['focus_keyword'] for r in records]

        titles = await generate_blog_titles(topic, locked_keywords)
        
        if not titles:
            await wait_msg.edit_text("❌ متأسفانه عنوانی تولید نشد. لطفاً موضوع دیگری امتحان کنید.")
            return

        kb = []
        for i, title in enumerate(titles):
            await state.update_data(**{f"title_{i}": title})
            kb.append([InlineKeyboardButton(text=title, callback_data=f"gbtitle_{i}")])
            
        kb.append([InlineKeyboardButton(text="❌ لغو عملیات", callback_data="gb_cancel")])
        
        await state.set_state(GeminiArticleWizard.waiting_for_title_selection)
        await wait_msg.edit_text(
            "✨ <b>عناوین پیشنهادی ضد-تکرار Gemini آماده است!</b>\n\n"
            "روی بهترین عنوان کلیک کنید تا نگارش مقاله آغاز شود:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode="HTML"
        )
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در تولید عنوان:\n<code>{str(e)[:300]}</code>", parse_mode="HTML")
        await state.clear()

# ==========================================
# ۳. انتخاب عنوان، تزریق کاتالوگ و نگارش مقاله
# ==========================================
@router.callback_query(F.data.startswith("gbtitle_"), GeminiArticleWizard.waiting_for_title_selection)
async def process_title_selection(callback: CallbackQuery, state: FSMContext, bot: Bot):
    title_index = callback.data.split("_")[1]
    data = await state.get_data()
    selected_title = data.get(f"title_{title_index}")
    
    await state.update_data(selected_title=selected_title)
    wait_msg = await callback.message.edit_text(
        f"📝 <b>عنوان انتخاب شد:</b>\n{selected_title}\n\n"
        "⏳ در حال واکشی کاتالوگ محصولات از دیتابیس اختصاصی (Supabase)..."
    )
    
    try:
        pool = await db_service.get_pool()
        
        # 🌟 ۱. واکشی کل کاتالوگ محصولات با اولویت محصولاتی که کمتر لینک گرفته‌اند
        all_products = []
        async with pool.acquire() as conn:
            records = await conn.fetch('SELECT product_id, name, permalink, image_url FROM products ORDER BY mention_count ASC')
            all_products = [dict(r) for r in records]
            
            # 🌟 ۲. واکشی مجدد کلمات کلیدی ممنوعه
            seo_records = await conn.fetch('SELECT focus_keyword FROM seo_ledger WHERE focus_keyword IS NOT NULL')
            locked_keywords = [r['focus_keyword'] for r in seo_records]
        
        await wait_msg.edit_text("⏳ کاتالوگ کامل سایت در میلی‌ثانیه دریافت شد. Gemini در حال استدلال، نگارش مقاله و انتخاب هوشمند محصولات می‌باشد (این مرحله ممکن است ۱ دقیقه طول بکشد)...")
        
        # ارسال کاتالوگ و محدودیت‌ها به هوش مصنوعی
        article_data = await generate_blog_article(selected_title, all_products, locked_keywords)
        await state.update_data(article_data=article_data)
        
        preview_html = (
            f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
            f"<title>{article_data['seo_title']}</title></head>"
            f"<body style='font-family:Tahoma; max-width:800px; margin:auto; line-height:2;'>"
            f"<h1>{selected_title}</h1>"
            f"{article_data['content_html']}</body></html>"
        ).encode('utf-8')
        
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=BufferedInputFile(preview_html, filename="article_preview.html"),
            caption="📄 پیش‌نمایش مقاله (همراه با لینک‌سازی‌ها، تصاویر و بخش FAQ)"
        )
        
        # استخراج آیدی محصولاتی که هوش مصنوعی در متن استفاده کرده است
        used_ids = article_data.get('used_product_ids', [])
        
        await state.set_state(GeminiArticleWizard.waiting_for_featured_image)
        await wait_msg.edit_text(
            f"✅ <b>مقاله با موفقیت تولید شد!</b>\n\n"
            f"🔑 کلمه کلیدی: <code>{article_data['focus_keyword']}</code>\n"
            f"🔗 نامک (Slug): <code>{article_data['slug']}</code>\n"
            f"🎯 تعداد محصولات لینک‌شده: <b>{len(used_ids)}</b> مورد\n\n"
            f"🖼 <b>مرحله آخر:</b>\nبرای حفظ اصالت برند، لطفاً یک <b>عکس واقعی و باکیفیت</b> برای تصویر شاخص این مقاله ارسال کنید:",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در نگارش مقاله:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
        await state.clear()

# ==========================================
# ۴. دریافت عکس واقعی، سئوی عکس و دریافت دسته‌بندی‌ها
# ==========================================
@router.message(GeminiArticleWizard.waiting_for_featured_image, F.photo | F.document)
async def process_article_image(message: Message, state: FSMContext, bot: Bot):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id
        
    if not file_id:
        await message.answer("❌ لطفاً یک تصویر معتبر ارسال کنید.")
        return

    wait_msg = await message.answer("⏳ در حال آپلود تصویر و دریافت دسته‌بندی‌های سایت...")
    
    try:
        data = await state.get_data()
        article_data = data['article_data']
        
        file_info = await bot.get_file(file_id)
        ext = file_info.file_path.split('.')[-1].lower() if '.' in file_info.file_path else 'jpg'
        file_bytes = io.BytesIO()
        await bot.download_file(file_info.file_path, file_bytes)
        
        media_id = await wp_service.upload_media(
            file_bytes.getvalue(), 
            f"{article_data['slug']}-featured.{ext}", 
            article_data['focus_keyword'], 
            article_data['focus_keyword']
        )
        await state.update_data(featured_media_id=media_id)
        
        wp_categories = await wp_service.get_categories()
        
        builder = InlineKeyboardBuilder()
        for cat in wp_categories:
            builder.button(text=cat['name'], callback_data=f"gbcat_{cat['id']}")
                
        builder.button(text="❌ لغو عملیات", callback_data="gb_cancel")
        builder.adjust(1)
        
        await state.set_state(GeminiArticleWizard.waiting_for_category)
        await wait_msg.edit_text(
            "✅ <b>تصویر شاخص با موفقیت آپلود و سئو شد.</b>\n\n"
            "🗂 لطفاً <b>دسته‌بندی مقاله</b> را از لیست زیر انتخاب کنید:",
            reply_markup=builder.as_markup(), parse_mode="HTML"
        )
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در آپلود تصویر یا دریافت دسته‌بندی:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

# ==========================================
# ۵. انتخاب دسته، ساخت مقاله و به‌روزرسانی دیتابیس سئو
# ==========================================
@router.callback_query(F.data.startswith("gbcat_"), GeminiArticleWizard.waiting_for_category)
async def process_category_and_create_post(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    wait_msg = await callback.message.edit_text("⏳ در حال ساخت ساختار نهایی مقاله و تزریق برچسب‌ها در وردپرس...")
    
    try:
        data = await state.get_data()
        article_data = data['article_data']
        selected_title = data['selected_title']
        tags_str = "، ".join(article_data.get('tags', []))
        
        # ۱. تبدیل نام برچسب‌ها به آیدی عددی در وردپرس
        tag_ids = []
        for tag_name in article_data.get('tags', []):
            t_id = await wp_service.get_or_create_tag(tag_name)
            if t_id:
                tag_ids.append(t_id)
        
        meta_data = {
            "rank_math_focus_keyword": article_data['focus_keyword'],
            "rank_math_title": article_data['seo_title'],
            "rank_math_description": article_data['meta_description']
        }
        
        payload = {
            "title": selected_title,
            "content": article_data['content_html'],
            "status": "draft",
            "slug": article_data['slug'],
            "categories": [cat_id],
            "tags": tag_ids,
            "featured_media": data['featured_media_id'],
            "meta": meta_data
        }
        
        # ۲. ایجاد پیش‌نویس در وردپرس
        post_result = await wp_service.create_post(payload)
        post_id = post_result['id']
        post_link = post_result.get('link', '')
        
        # 🌟 ۳. ثبت کلمه کلیدی در حافظه سئو (SEO Ledger) و آپدیت شمارنده محصولات
        try:
            pool = await db_service.get_pool()
            async with pool.acquire() as conn:
                # قفل کردن کلمه کلیدی جدید
                await conn.execute('''
                    INSERT INTO seo_ledger (focus_keyword, slug, title, tags)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (focus_keyword) DO NOTHING
                ''', article_data['focus_keyword'], article_data['slug'], selected_title, tags_str)
                
                # اضافه کردن به تعداد منشن محصولاتی که هوش مصنوعی استفاده کرد
                used_ids = article_data.get('used_product_ids', [])
                for pid in used_ids:
                    await conn.execute('UPDATE products SET mention_count = mention_count + 1 WHERE product_id = $1', pid)
        except Exception:
            pass # ایزوله کردن خطاهای احتمالی دیتابیس برای جلوگیری از توقف ربات
        
        await state.update_data(post_id=post_id, post_title=selected_title, post_link=post_link)
        await state.set_state(GeminiArticleWizard.waiting_for_publish_action)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار عمومی در سایت", callback_data="gbaction_publish")],
            [InlineKeyboardButton(text="🗑 انتقال به زباله‌دان", callback_data="gbaction_trash")]
        ])
        
        await wait_msg.edit_text(
            f"🎉 <b>مقاله شما با موفقیت به عنوان پیش‌نویس در سایت ایجاد شد!</b>\n\n"
            f"🏷 <b>عنوان:</b> {selected_title}\n"
            f"🧠 <b>وضعیت سئو:</b> کلمه کلیدی در دیتابیس قفل شد.\n"
            f"🔗 <b>بک‌لینک‌ها:</b> شمارنده محصولات آپدیت شد.\n"
            f"🔖 <b>برچسب‌ها:</b> {tags_str}\n\n"
            f"انتخاب کنید:",
            reply_markup=kb, parse_mode="HTML"
        )
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ساخت مقاله:\n<code>{str(e)[:300]}</code>", parse_mode="HTML")

# ==========================================
# ۶. داشبورد نهایی (انتشار / زباله‌دان)
# ==========================================
@router.callback_query(F.data.startswith("gbaction_"), GeminiArticleWizard.waiting_for_publish_action)
async def process_publish_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[1]
    data = await state.get_data()
    post_id = data['post_id']
    post_link = data['post_link']
    
    try:
        await wp_service.update_post(post_id, {"status": action})
        
        if action == "publish":
            msg = f"🚀 <b>مقاله با موفقیت منتشر شد!</b>\n\n🌐 <a href='{post_link}'>مشاهده مقاله در سایت</a>"
        else:
            msg = f"🗑 مقاله به زباله‌دان سایت منتقل شد."
            
        await callback.message.edit_text(msg, parse_mode="HTML", disable_web_page_preview=True)
        await state.clear()
        
    except Exception as e:
        await callback.answer(f"❌ خطا: {str(e)[:50]}", show_alert=True)

@router.callback_query(F.data == "gb_cancel")
async def cancel_gemini_blog(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ عملیات تولید مقاله لغو شد.")
    await callback.answer()
