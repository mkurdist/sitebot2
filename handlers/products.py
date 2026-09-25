import re  
import io
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from states.product_states import ProductWizard

# استفاده از سرویس‌های جدید و ایزوله
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service

router = Router()

# ==========================================
# کیبورد داشبورد شیشه‌ای (ارتقا یافته با گالری)
# ==========================================
def get_dashboard_keyboard(product_id: int, product_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📝 توضیحات", callback_data=f"edit_desc_{product_id}"),
            InlineKeyboardButton(text="🖼 تصویر اصلی", callback_data=f"edit_img_{product_id}")
        ],
        [
            InlineKeyboardButton(text="🗂 گالری تصاویر", callback_data=f"edit_gallery_{product_id}"),
            InlineKeyboardButton(text="📂 دسته‌بندی", callback_data=f"edit_cat_{product_id}")
        ],
        [
            InlineKeyboardButton(text="💰 قیمت و موجودی", callback_data=f"edit_price_{product_id}"),
            InlineKeyboardButton(text="🔍 تنظیمات سئو", callback_data=f"edit_seo_{product_id}")
        ],
        [
            InlineKeyboardButton(text="✅ انتشار نهایی", callback_data=f"publish_{product_id}"),
            InlineKeyboardButton(text="❌ حذف", callback_data=f"delete_{product_id}")
        ]
    ])

# ==========================================
# شروع عملیات ثبت محصول دستی
# ==========================================
@router.message(F.text == "➕ محصول جدید")
async def start_product_wizard(message: Message, state: FSMContext):
    data = await state.get_data()
    active_product_id = data.get("product_id")
    active_product_name = data.get("product_name")

    if active_product_id:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 ادامه ویرایش قبلی", callback_data=f"resume_prod_{active_product_id}"),
                InlineKeyboardButton(text="➕ ساخت جدید", callback_data="force_new_prod")
            ]
        ])
        await message.answer(
            f"⚠️ <b>شما یک محصول در حال ویرایش دارید!</b>\n\n"
            f"🏷 نام: {active_product_name or 'بدون نام'}\n"
            f"🆔 آیدی: <code>{active_product_id}</code>\n\n"
            f"می‌خواهید کار روی این محصول را ادامه دهید یا محصول جدیدی بسازید؟",
            reply_markup=keyboard, parse_mode="HTML"
        )
        return

    await state.clear()
    await state.set_state(ProductWizard.waiting_for_name)
    await message.answer("🛒 <b>ساخت محصول جدید</b>\n\nلطفاً فقط <b>نام محصول</b> را وارد کنید (مثلاً کاسه سفالی میناکاری):", parse_mode="HTML")

@router.callback_query(F.data == "force_new_prod")
async def force_new_product(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProductWizard.waiting_for_name)
    await callback.message.edit_text("🛒 <b>ساخت محصول جدید</b>\n\nلطفاً فقط <b>نام محصول</b> را وارد کنید (مثلاً کاسه سفالی میناکاری):", parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("resume_prod_"))
async def resume_product(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    try:
        product = await wc_service.get_product(product_id)
        await state.update_data(product_id=product_id, product_name=product['name'])
        await callback.message.edit_text(
            f"📦 <b>بازگشت به ویرایش محصول</b>\n\n"
            f"🏷 <b>نام:</b> {product['name']}\n"
            f"🆔 <b>آیدی:</b> <code>{product_id}</code>\n\n"
            f"👇 از پنل زیر استفاده کنید:",
            reply_markup=get_dashboard_keyboard(product_id, product['name']), parse_mode="HTML"
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ خطا در بازیابی محصول:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
    await callback.answer()

@router.message(F.text == "/cancel")
async def cancel_wizard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات لغو شد.")

@router.message(ProductWizard.waiting_for_name)
async def process_initial_name(message: Message, state: FSMContext):
    product_name = message.text
    wait_msg = await message.answer("⏳ در حال ایجاد فضای پیش‌نویس در سایت...")
    try:
        product_data = {"name": product_name, "type": "simple", "status": "draft"}
        result = await wc_service.create_simple_product(product_data)
        product_id = result['id']
        
        await state.update_data(product_id=product_id, product_name=product_name)
        
        text = (
            f"📦 <b>محصول ایجاد شد (پیش‌نویس)</b>\n\n"
            f"🏷 <b>نام:</b> {product_name}\n"
            f"🆔 <b>آیدی:</b> <code>{product_id}</code>\n\n"
            f"👇 حالا از پنل زیر، هر بخشی را که می‌خواهید تکمیل کنید:"
        )
        await wait_msg.edit_text(text, reply_markup=get_dashboard_keyboard(product_id, product_name), parse_mode="HTML")
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ساخت پیش‌نویس:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
        await state.clear()

# ==========================================
# سیستم هوشمند افزودن خودکار محصول (Auto Add) - مجهز به سیستم بافر متنی
# ==========================================
@router.message(F.text == "⚡ افزودن خودکار (AI)")
async def start_auto_add(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProductWizard.waiting_for_ai_text)
    await state.update_data(ai_buffer="")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ پردازش و ساخت محصول", callback_data="process_ai_buffer")]
    ])
    
    instruction = (
        "🤖 <b>سیستم افزودن خودکار محصول فعال شد</b>\n\n"
        "لطفاً کل متن تولید شده توسط هوش مصنوعی را در اینجا Paste کنید.\n"
        "<i>(نکته: اگر متن خیلی طولانی بود و تلگرام آن را دو تکه کرد، هر دو پیام را بفرستید. ربات خودش آن‌ها را سرهم می‌کند.)</i>\n\n"
        "پس از ارسال تمام بخش‌های متن، روی دکمه زیر کلیک کنید تا ربات کارش را شروع کند:"
    )
    await message.answer(instruction, reply_markup=keyboard, parse_mode="HTML")

@router.message(ProductWizard.waiting_for_ai_text)
async def accumulate_ai_text(message: Message, state: FSMContext):
    data = await state.get_data()
    current_buffer = data.get("ai_buffer", "")
    new_buffer = current_buffer + "\n\n" + message.text
    await state.update_data(ai_buffer=new_buffer)
    
    await message.answer("📥 <i>متن دریافت و به حافظه اضافه شد. اگر بخش دیگری مانده بفرستید، در غیر این صورت دکمه پردازش را از پیام بالا بزنید.</i>", parse_mode="HTML")

@router.callback_query(F.data == "process_ai_buffer")
async def process_ai_auto_add(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data.get("ai_buffer", "")
    
    if not text.strip():
        await callback.answer("❌ هنوز هیچ متنی نفرستاده‌اید!", show_alert=True)
        return

    wait_msg = await callback.message.answer("⏳ در حال تحلیل متن هوش مصنوعی و ساخت محصول در ووکامرس...")
    
    try:
        title = re.search(r'۱\.\s*کادر عنوان محصول.*?\n(.*?)(?=\n۲\.)', text, re.DOTALL)
        focus_kw = re.search(r'۲\.\s*کادر کلمه کلیدی اصلی.*?\n(.*?)(?=\n۳\.)', text, re.DOTALL)
        short_desc_ai = re.search(r'۳\.\s*کادر توضیحات کوتاه.*?\n(.*?)(?=\n۴\.)', text, re.DOTALL)
        conv_text = re.search(r'۴\.\s*کادر متن محاوره‌ای.*?\n(.*?)(?=\n۵\.)', text, re.DOTALL)
        full_desc = re.search(r'۵\.\s*کادر توضیحات کامل.*?\n(.*?)(?=\n۶\.)', text, re.DOTALL)
        specs = re.search(r'۶\.\s*کادر ویژگی‌ها.*?\n(.*?)(?=\n۷\.)', text, re.DOTALL)
        
        seo_title = re.search(r'عنوان سئو.*?\):\s*(.*?)\n', text)
        seo_desc = re.search(r'توضیحات متادیسکریپشن.*?\):\s*(.*?)\n', text)
        slug = re.search(r'نامک انگلیسی.*?\):\s*(.*?)\n', text)
        tags_match = re.search(r'۸\.\s*کادر برچسب‌های محصول.*?\n(.*?)(?=\n۹\.)', text, re.DOTALL)

        product_title = title.group(1).strip() if title else "محصول جدید AI"
        
        short_description = ""
        if specs: 
            short_description = f"<b>مشخصات و ویژگی‌ها:</b><br><br>{specs.group(1).strip().replace('\n', '<br>')}"

        long_description = ""
        if short_desc_ai: long_description += f"{short_desc_ai.group(1).strip()}<br><br>"
        if conv_text: long_description += f"<i>{conv_text.group(1).strip()}</i><br><br>"
        if full_desc: long_description += f"{full_desc.group(1).strip().replace('\n', '<br>')}"

        tags_list = []
        if tags_match:
            raw_tags = tags_match.group(1).split('،') if '،' in tags_match.group(1) else tags_match.group(1).split(',')
            tags_list = [{"name": t.strip()} for t in raw_tags if t.strip()]

        meta_data = []
        if focus_kw: meta_data.append({"key": "rank_math_focus_keyword", "value": focus_kw.group(1).strip()})
        if seo_title: meta_data.append({"key": "rank_math_title", "value": seo_title.group(1).strip()})
        if seo_desc: meta_data.append({"key": "rank_math_description", "value": seo_desc.group(1).strip()})

        product_payload = {
            "name": product_title,
            "type": "simple",
            "status": "draft",
            "short_description": short_description,
            "description": long_description,
            "tags": tags_list,
            "meta_data": meta_data
        }
        
        if slug:
            product_payload["slug"] = slug.group(1).strip()

        result = await wc_service.create_simple_product(product_payload)
        product_id = result['id']
        
        await state.update_data(product_id=product_id, product_name=product_title)
        
        success_msg = (
            f"✅ <b>جادوی AI انجام شد! محصول با موفقیت ایجاد گردید.</b>\n\n"
            f"🏷 <b>نام:</b> {product_title}\n"
            f"🆔 <b>آیدی:</b> <code>{product_id}</code>\n\n"
            f"🧩 تمام متون، تگ‌ها و سئو جای‌گذاری شدند.\n"
            f"👇 حالا فقط کافیست از پنل زیر <b>تصاویر</b>، <b>قیمت</b> و <b>دسته‌بندی</b> را مشخص کرده و انتشار را بزنید:"
        )
        
        await wait_msg.edit_text(success_msg, reply_markup=get_dashboard_keyboard(product_id, product_title), parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        await wait_msg.edit_text(
            f"❌ خطایی در خواندن متن یا ارتباط با سایت رخ داد.\n\nجزئیات خطا:\n<code>{str(e)[:500]}</code>",
            parse_mode="HTML"
        )
        await state.clear()
        await callback.answer()

# ==========================================
# مدیریت دکمه‌های انتشار و حذف
# ==========================================
@router.callback_query(F.data.startswith("publish_"))
async def process_publish(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[1])
    await callback.message.edit_text("⏳ در حال انتشار روی سایت...")
    try:
        result = await wc_service.update_product(product_id, {"status": "publish"})
        await state.clear()
        await callback.message.edit_text(
            f"✅ <b>محصول با موفقیت در سایت منتشر شد! 🎉</b>\n\n"
            f"🌐 <a href='{result['permalink']}'>برای مشاهده صفحه محصول کلیک کنید</a>",
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ خطا در انتشار:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("delete_"))
async def process_delete(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[1])
    await callback.message.edit_text("⏳ در حال انتقال به زباله‌دان...")
    try:
        await wc_service.delete_product(product_id)
        await state.clear() 
        await callback.message.edit_text(f"🗑 محصول با موفقیت به زباله‌دان سایت منتقل شد.", parse_mode="HTML")
    except Exception as e:
        await callback.message.edit_text(f"❌ خطا در حذف:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

# ==========================================
# دکمه: گالری تصاویر 🗂 
# ==========================================
@router.callback_query(F.data.startswith("edit_gallery_"))
async def start_edit_gallery(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(ProductWizard.waiting_for_gallery_image)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 اتمام و بازگشت به داشبورد", callback_data=f"finish_gallery_{product_id}")]
    ])
    
    await callback.message.answer(
        "🗂 <b>افزودن تصویر به گالری محصول</b>\n\n"
        "لطفاً عکس مورد نظر گالری را بفرستید (عکس معمولی یا فایل WebP).\n"
        "می‌توانید چند عکس به نوبت بفرستید و در نهایت روی دکمه اتمام کلیک کنید:",
        reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()

@router.message(ProductWizard.waiting_for_gallery_image, F.photo | F.document)
async def process_gallery_image_file(message: Message, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        doc = message.document
        if doc.mime_type and "image" in doc.mime_type or doc.file_name.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
            file_id = doc.file_id
            
    if not file_id:
        await message.answer("❌ فرمت فایل ارسالی معتبر نیست. لطفاً یک تصویر ارسال کنید.")
        return

    await state.update_data(gallery_file_id=file_id)
    await state.set_state(ProductWizard.waiting_for_gallery_alt)
    await message.answer("📝 لطفاً <b>متن جایگزین (Alt Text)</b> این عکس گالری را وارد کنید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_gallery_image)
async def process_gallery_image_invalid(message: Message):
    await message.answer("❌ لطفاً یک تصویر یا فایل تصویری معتبر بفرستید.")

@router.message(ProductWizard.waiting_for_gallery_alt)
async def process_gallery_alt(message: Message, state: FSMContext):
    await state.update_data(gallery_alt=message.text)
    await state.set_state(ProductWizard.waiting_for_gallery_title)
    await message.answer("🏷 حالا <b>عنوان تصویر (Title)</b> این عکس گالری را وارد کنید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_gallery_title)
async def process_gallery_title(message: Message, state: FSMContext, bot: Bot):
    title_text = message.text
    data = await state.get_data()
    product_id = data['product_id']
    file_id = data['gallery_file_id']
    alt_text = data['gallery_alt']
    
    wait_msg = await message.answer("⏳ در حال آپلود و اتصال به گالری محصول...")
    try:
        product_name = data.get('product_name', f'product-{product_id}')
        seo_slug = re.sub(r'[\s_]+', '-', product_name.strip()) or f'product-{product_id}'

        file_info = await bot.get_file(file_id)
        ext = file_info.file_path.split('.')[-1].lower() if '.' in file_info.file_path else 'jpg'
        file_bytes = io.BytesIO()
        await bot.download_file(file_info.file_path, file_bytes)

        seo_filename = f"{seo_slug}-gallery-{file_info.file_unique_id[-4:]}.{ext}"
        media_id = await wp_service.upload_media(file_bytes.getvalue(), seo_filename, alt_text, title_text)
        
        product = await wc_service.get_product(product_id)
        current_images = product.get('images', [])
        
        new_img_payload = {
            "id": media_id,
            "name": title_text,
            "alt": alt_text
        }
        current_images.append(new_img_payload)
        
        await wc_service.update_product(product_id, {"images": current_images})

        await state.set_state(ProductWizard.waiting_for_gallery_image)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 اتمام و بازگشت به داشبورد", callback_data=f"finish_gallery_{product_id}")]
        ])
        
        await wait_msg.edit_text(
            f"✅ <b>عکس با موفقیت به گالری محصول متصل شد!</b>\n\n"
            f"اگر عکس دیگری برای گالری دارید بفرستید، در غیر این صورت روی دکمه زیر کلیک کنید:",
            reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ثبت گالری:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
        await state.set_state(ProductWizard.waiting_for_gallery_image)

@router.callback_query(F.data.startswith("finish_gallery_"))
async def finish_gallery_selection(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    data = await state.get_data()
    product_name = data.get('product_name')
    wait_msg = await callback.message.edit_text("⏳ در حال بارگذاری داشبورد...")
    try:
        if not product_name:
            product = await wc_service.get_product(product_id)
            product_name = product['name']
        await wait_msg.edit_text(
            f"✅ <b>گالری تصاویر به‌روزرسانی شد.</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, product_name), parse_mode="HTML"
        )
    except:
        pass

# ==========================================
# دکمه: تصویر اصلی 🖼 
# ==========================================
@router.callback_query(F.data.startswith("edit_img_"))
async def start_edit_img(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(ProductWizard.waiting_for_image)
    await callback.message.answer(
        "🖼 <b>آپلود تصویر اصلی</b>\n\n"
        "لطفاً تصویر خود را بفرستید (عکس معمولی یا فایل WebP):", parse_mode="HTML"
    )
    await callback.answer()

@router.message(ProductWizard.waiting_for_image, F.photo | F.document)
async def process_image_file(message: Message, state: FSMContext, bot: Bot):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        doc = message.document
        if doc.mime_type and "image" in doc.mime_type or doc.file_name.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
            file_id = doc.file_id
            
    if not file_id:
        await message.answer("❌ فرمت فایل ارسالی معتبر نیست. لطفاً یک تصویر ارسال کنید.")
        return

    await state.update_data(file_id=file_id)
    await state.set_state(ProductWizard.waiting_for_image_alt)
    await message.answer("📝 لطفاً <b>متن جایگزین (Alt Text)</b> تصویر اصلی را وارد کنید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_image)
async def process_image_invalid(message: Message):
    await message.answer("❌ لطفاً حتماً یک تصویر یا فایل تصویری ارسال کنید.")

@router.message(ProductWizard.waiting_for_image_alt)
async def process_image_alt(message: Message, state: FSMContext):
    await state.update_data(alt_text=message.text)
    await state.set_state(ProductWizard.waiting_for_image_title)
    await message.answer("🏷 حالا <b>عنوان تصویر (Title)</b> تصویر اصلی را وارد کنید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_image_title)
async def process_image_title(message: Message, state: FSMContext, bot: Bot):
    title_text = message.text
    data = await state.get_data()
    product_id = data['product_id']
    file_id = data['file_id']
    alt_text = data['alt_text']
    
    wait_msg = await message.answer("⏳ در حال آپلود تصویر در سایت...")
    try:
        product_name = data.get('product_name', f'product-{product_id}')
        seo_slug = re.sub(r'[\s_]+', '-', product_name.strip()) or f'product-{product_id}'

        file_info = await bot.get_file(file_id)
        ext = file_info.file_path.split('.')[-1].lower() if '.' in file_info.file_path else 'jpg'
        file_bytes = io.BytesIO()
        await bot.download_file(file_info.file_path, file_bytes)

        seo_filename = f"{seo_slug}-{file_info.file_unique_id[-4:]}.{ext}"
        media_id = await wp_service.upload_media(file_bytes.getvalue(), seo_filename, alt_text, title_text)

        product = await wc_service.get_product(product_id)
        existing_images = product.get('images', [])
        
        main_image = {
            "id": media_id,
            "name": title_text,
            "alt": alt_text
        }
        
        if existing_images:
            existing_images[0] = main_image
        else:
            existing_images = [main_image]
            
        update_data = {"images": existing_images}
        
        await wc_service.update_product(product_id, update_data)
        
        await wait_msg.edit_text(
            f"✅ <b>تصویر اصلی با مشخصات کامل ثبت شد!</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, product_name), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ثبت تصویر:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

# ==========================================
# دکمه: تنظیمات سئو 🔍
# ==========================================
@router.callback_query(F.data.startswith("edit_seo_"))
async def start_edit_seo(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(ProductWizard.waiting_for_seo_keyword)
    await callback.message.answer("🔍 <b>تنظیمات سئو (Rank Math)</b>\n\nابتدا <b>کلمه کلیدی اصلی</b> (Focus Keyword) را وارد کنید:", parse_mode="HTML")
    await callback.answer()

@router.message(ProductWizard.waiting_for_seo_keyword)
async def process_seo_keyword(message: Message, state: FSMContext):
    await state.update_data(seo_keyword=message.text)
    await state.set_state(ProductWizard.waiting_for_seo_slug)
    await message.answer("🔗 حالا <b>پیوند دایمی (Slug / نامک)</b> را وارد کنید (مثلاً <code>blue-ceramic-bowl</code>):", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_seo_slug)
async def process_seo_slug(message: Message, state: FSMContext):
    await state.update_data(seo_slug=message.text)
    await state.set_state(ProductWizard.waiting_for_seo_title)
    await message.answer("📝 بسیار عالی. حالا <b>عنوان سئو (SEO Title)</b> را بفرستید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_seo_title)
async def process_seo_title(message: Message, state: FSMContext):
    await state.update_data(seo_title=message.text)
    await state.set_state(ProductWizard.waiting_for_seo_desc)
    await message.answer("📄 در نهایت، <b>توضیحات متا (Meta Description)</b> را بفرستید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_seo_desc)
async def process_seo_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data['product_id']
    keyword = data['seo_keyword']
    slug = data['seo_slug']
    title = data['seo_title']
    desc = message.text
    
    wait_msg = await message.answer("⏳ در حال ثبت اطلاعات سئو و پیوند دایمی در سایت...")
    try:
        meta_data = [
            {"key": "rank_math_focus_keyword", "value": keyword},
            {"key": "rank_math_title", "value": title},
            {"key": "rank_math_description", "value": desc}
        ]
        
        update_payload = {
            "slug": slug,
            "meta_data": meta_data
        }
        
        await wc_service.update_product(product_id, update_payload)
        
        await wait_msg.edit_text(
            f"✅ <b>اطلاعات سئو و پیوند دایمی با موفقیت ثبت شد!</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, data.get('product_name', 'محصول')), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ثبت سئو:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

# ==========================================
# دکمه: دسته‌بندی 📂
# ==========================================
@router.callback_query(F.data.startswith("edit_cat_"))
async def start_edit_cat(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    wait_msg = await callback.message.edit_text("⏳ در حال دریافت لیست دسته‌بندی‌ها از سایت...")
    try:
        categories = await wc_service.get_categories()
        await state.update_data(product_id=product_id, all_cats=categories, selected_cats=[])
        
        builder = InlineKeyboardBuilder()
        for cat in categories:
            builder.button(text=f"[ ] {cat['name']}", callback_data=f"togglecat_{cat['id']}")
        
        builder.button(text="✅ تایید و ادامه انتخاب دسته اصلی ➡️", callback_data="finish_cat_selection")
        builder.button(text="🔙 بازگشت", callback_data=f"backdash_{product_id}")
        builder.adjust(1)
        
        await wait_msg.edit_text(
            "📂 <b>انتخاب دسته‌بندی‌ها:</b>\n\n"
            "روی هر دسته کلیک کنید تا انتخاب شود. پس از اتمام، روی دکمه تایید کلیک کنید:",
            reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در دریافت دسته‌بندی:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("togglecat_"))
async def process_toggle_cat(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    selected = data.get("selected_cats", [])
    all_cats = data.get("all_cats", [])
    product_id = data.get("product_id")
    
    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)
        
    await state.update_data(selected_cats=selected)
    
    builder = InlineKeyboardBuilder()
    for cat in all_cats:
        status = "✅" if cat['id'] in selected else "[ ]"
        builder.button(text=f"{status} {cat['name']}", callback_data=f"togglecat_{cat['id']}")
        
    builder.button(text="✅ تایید و ادامه انتخاب دسته اصلی ➡️", callback_data="finish_cat_selection")
    builder.button(text="🔙 بازگشت", callback_data=f"backdash_{product_id}")
    builder.adjust(1)
    
    try:
        await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    except:
        pass
    await callback.answer()

@router.callback_query(F.data == "finish_cat_selection")
async def process_finish_cat_selection(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_cats", [])
    all_cats = data.get("all_cats", [])
    
    if not selected:
        await callback.answer("❌ لطفاً حداقل یک دسته‌بندی انتخاب کنید!", show_alert=True)
        return
        
    builder = InlineKeyboardBuilder()
    for cat in all_cats:
        if cat['id'] in selected:
            builder.button(text=cat['name'], callback_data=f"setprimary_{cat['id']}")
            
    builder.adjust(1)
    await callback.message.edit_text(
        "⭐ حالا <b>دسته اصلی (Primary)</b> این محصول را مشخص کنید:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("setprimary_"))
async def process_set_primary_cat(callback: CallbackQuery, state: FSMContext):
    primary_cat_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    product_id = data.get("product_id")
    selected_cats = data.get("selected_cats", [])
    
    wait_msg = await callback.message.edit_text("⏳ در حال ثبت دسته‌بندی‌ها و دسته اصلی در سایت...")
    try:
        categories_payload = [{"id": cid} for cid in selected_cats]
        update_data = {
            "categories": categories_payload,
            "meta_data": [
                {"key": "rank_math_primary_product_cat", "value": str(primary_cat_id)}
            ]
        }
        await wc_service.update_product(product_id, update_data)
        
        await wait_msg.edit_text(
            f"✅ <b>دسته‌بندی‌ها ثبت شد!</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, data.get('product_name', 'محصول')), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ثبت دسته‌بندی:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("backdash_"))
async def process_backdash(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    product_name = data.get('product_name')
    wait_msg = await callback.message.edit_text("⏳ در حال بازگشت...")
    try:
        if not product_name:
            product = await wc_service.get_product(product_id)
            product_name = product['name']
        await wait_msg.edit_text(
            f"👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, product_name), parse_mode="HTML"
        )
    except:
        pass

# ==========================================
# دکمه: توضیحات 📝
# ==========================================
@router.callback_query(F.data.startswith("edit_desc_"))
async def start_edit_desc(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(ProductWizard.waiting_for_short_desc)
    await callback.message.answer("📝 لطفاً <b>توضیحات کوتاه</b> محصول را بفرستید:", parse_mode="HTML")
    await callback.answer()

@router.message(ProductWizard.waiting_for_short_desc)
async def process_short_desc(message: Message, state: FSMContext):
    html_text = message.html_text.replace("\n", "<br>")
    await state.update_data(short_desc=html_text)
    await state.set_state(ProductWizard.waiting_for_long_desc)
    await message.answer("📄 بسیار عالی. حالا <b>توضیحات کامل</b> محصول را بفرستید:", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_long_desc)
async def process_long_desc(message: Message, state: FSMContext):
    html_text = message.html_text.replace("\n", "<br>")
    data = await state.get_data()
    product_id = data['product_id']
    wait_msg = await message.answer("⏳ در حال ثبت توضیحات در سایت...")
    try:
        update_data = {"short_description": data['short_desc'], "description": html_text}
        await wc_service.update_product(product_id, update_data)
        
        await wait_msg.edit_text(
            f"✅ <b>توضیحات محصول ذخیره شد!</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, data.get('product_name', 'محصول')), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در ثبت توضیحات:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")

# ==========================================
# دکمه: قیمت و موجودی 💰
# ==========================================
@router.callback_query(F.data.startswith("edit_price_"))
async def start_edit_price(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(ProductWizard.waiting_for_price)
    await callback.message.answer("💰 لطفاً <b>قیمت اصلی محصول</b> را به تومان وارد کنید (فقط عدد):", parse_mode="HTML")
    await callback.answer()

@router.message(ProductWizard.waiting_for_price)
async def process_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً قیمت را فقط به صورت عدد وارد کنید!")
        return
    await state.update_data(price=message.text)
    await state.set_state(ProductWizard.waiting_for_stock)
    await message.answer("📦 حالا <b>موجودی انبار</b> را وارد کنید (فقط عدد):", parse_mode="HTML")

@router.message(ProductWizard.waiting_for_stock)
async def process_stock(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً موجودی را فقط به صورت عدد وارد کنید!")
        return
    data = await state.get_data()
    product_id, price, stock = data['product_id'], data['price'], int(message.text)
    wait_msg = await message.answer("⏳ در حال ثبت قیمت و موجودی در سایت...")
    try:
        update_data = {"regular_price": str(price), "manage_stock": True, "stock_quantity": stock}
        await wc_service.update_product(product_id, update_data)
        
        await wait_msg.edit_text(
            f"✅ <b>قیمت و موجودی تنظیم شد!</b>\n\n👇 داشبورد محصول:",
            reply_markup=get_dashboard_keyboard(product_id, data.get('product_name', 'محصول')), parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطا در تنظیم قیمت:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
