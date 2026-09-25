import asyncio
from aiogram import Router, F
from aiogram.types import Message
from services.woocommerce import wc_service_instance as wc_service
from services.database import db_service

router = Router()

# ==========================================
# دستور مخفی ادمین برای همگام‌سازی اولیه محصولات
# ==========================================
@router.message(F.text == "/sync_db")
async def start_db_sync(message: Message):
    await message.answer(
        "⏳ <b>در حال شروع همگام‌سازی قطره‌چکانی محصولات...</b>\n"
        "ربات هر بار ۲۰ محصول را واکشی کرده و ۳ ثانیه به هاست استراحت می‌دهد تا فشاری به سایت وارد نشود. "
        "لطفاً منتظر بمانید.",
        parse_mode="HTML"
    )
    
    page = 1
    per_page = 20
    total_synced = 0
    pool = await db_service.get_pool()

    while True:
        try:
            # واکشی محصولات از ووکامرس (صفحه به صفحه)
            products = await wc_service._request("GET", "products", params={"per_page": per_page, "page": page})
            
            if not products:
                break # محصولات تمام شد
            
            async with pool.acquire() as conn:
                for p in products:
                    p_id = p['id']
                    p_name = p['name']
                    p_slug = p['slug']
                    p_url = p['permalink']
                    
                    images = p.get('images', [])
                    p_image = images[0]['src'] if images else ""
                    
                    # ذخیره یا آپدیت در دیتابیس (mention_count صفر می‌ماند تا در مقالات استفاده شود)
                    await conn.execute('''
                        INSERT INTO products (product_id, name, slug, permalink, image_url, mention_count)
                        VALUES ($1, $2, $3, $4, $5, 0)
                        ON CONFLICT (product_id) DO UPDATE 
                        SET name = $2, slug = $3, permalink = $4, image_url = $5
                    ''', p_id, p_name, p_slug, p_url, p_image)
                    
                    total_synced += 1
            
            # 🌟 ۳ ثانیه استراحت مطلق برای محافظت از CPU و RAM هاست وردپرس شما
            await asyncio.sleep(3)
            page += 1
            
        except Exception as e:
            await message.answer(f"❌ خطا در صفحه {page}:\n<code>{str(e)[:200]}</code>", parse_mode="HTML")
            break
            
    await message.answer(
        f"✅ <b>همگام‌سازی با موفقیت پایان یافت!</b>\n"
        f"تعداد <b>{total_synced}</b> محصول به طور کامل در دیتابیس اختصاصی ربات (Supabase) بایگانی شد.",
        parse_mode="HTML"
    )
