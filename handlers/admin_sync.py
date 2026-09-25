import asyncio
from aiogram import Router, F
from aiogram.types import Message
from services.woocommerce import wc_service_instance as wc_service
from services.wordpress import wp_service_instance as wp_service  # 🌟 سرویس وردپرس اضافه شد
from services.database import db_service

router = Router()

# ==========================================
# ۱. همگام‌سازی اولیه محصولات
# ==========================================
@router.message(F.text == "/sync_db")
async def start_db_sync(message: Message):
    await message.answer(
        "⏳ <b>در حال شروع همگام‌سازی قطره‌چکانی محصولات...</b>\n"
        "ربات هر بار ۲۰ محصول را واکشی کرده و ۳ ثانیه استراحت می‌کند. لطفاً منتظر بمانید.",
        parse_mode="HTML"
    )
    
    page = 1
    per_page = 20
    total_synced = 0
    pool = await db_service.get_pool()

    while True:
        try:
            products = await wc_service.get_products(page=page, per_page=per_page)
            
            if not products:
                break
            
            async with pool.acquire() as conn:
                for p in products:
                    p_id = p['id']
                    p_name = p['name']
                    p_slug = p['slug']
                    p_url = p['permalink']
                    
                    images = p.get('images', [])
                    p_image = images[0]['src'] if images else ""
                    
                    await conn.execute('''
                        INSERT INTO products (product_id, name, slug, permalink, image_url, mention_count)
                        VALUES ($1, $2, $3, $4, $5, 0)
                        ON CONFLICT (product_id) DO UPDATE 
                        SET name = $2, slug = $3, permalink = $4, image_url = $5
                    ''', p_id, p_name, p_slug, p_url, p_image)
                    
                    total_synced += 1
            
            await asyncio.sleep(3)
            page += 1
            
        except Exception as e:
            await message.answer(f"❌ خطا در صفحه {page} محصولات:\n<code>{str(e)[:200]}</code>", parse_mode="HTML")
            break
            
    await message.answer(
        f"✅ <b>همگام‌سازی محصولات پایان یافت!</b>\n"
        f"تعداد <b>{total_synced}</b> محصول در دیتابیس ربات بایگانی شد.",
        parse_mode="HTML"
    )

# ==========================================
# ۲. همگام‌سازی مقالات و حافظه سئو (SEO Ledger)
# ==========================================
@router.message(F.text == "/sync_articles")
async def start_articles_sync(message: Message):
    await message.answer(
        "⏳ <b>در حال ساخت حافظه سئو (SEO Ledger)...</b>\n"
        "ربات در حال واکشی مقالات قبلی سایت است تا کلمات کلیدی آن‌ها را قفل کند و از تولید مقاله تکراری جلوگیری نماید.",
        parse_mode="HTML"
    )
    
    page = 1
    per_page = 20
    total_synced = 0
    pool = await db_service.get_pool()
    session = await wp_service.get_session()

    while True:
        try:
            url = f"{wp_service.base_url}/posts"
            # فقط مقالات منتشر شده را می‌گیریم
            params = {"per_page": per_page, "page": page, "status": "publish"}
            
            async with session.get(url, params=params) as response:
                if response.status in [400, 404]: # رسیدن به انتهای صفحات وردپرس
                    break
                if response.status != 200:
                    raise Exception(await response.text())
                
                posts = await response.json()
                if not posts:
                    break
                
                async with pool.acquire() as conn:
                    for post in posts:
                        title = post.get('title', {}).get('rendered', 'بدون عنوان')
                        slug = post.get('slug', '')
                        meta = post.get('meta', {})
                        
                        # استخراج کلمه کلیدی رنک‌مث (اگر خالی بود از نامک استفاده می‌کند)
                        focus_keyword = meta.get('rank_math_focus_keyword', '')
                        if not focus_keyword:
                            focus_keyword = slug
                            
                        tags_str = ",".join(map(str, post.get('tags', [])))

                        try:
                            # اگر کلمه کلیدی تکراری باشد، نادیده می‌گیرد (ON CONFLICT DO NOTHING)
                            await conn.execute('''
                                INSERT INTO seo_ledger (focus_keyword, slug, title, tags)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (focus_keyword) DO NOTHING
                            ''', focus_keyword, slug, title, tags_str)
                            total_synced += 1
                        except Exception:
                            pass # عبور از خطاهای جزئی هر مقاله
            
            await asyncio.sleep(3)
            page += 1
            
        except Exception as e:
            await message.answer(f"❌ خطا در صفحه {page} مقالات:\n<code>{str(e)[:200]}</code>", parse_mode="HTML")
            break
            
    await message.answer(
        f"✅ <b>حافظه سئو با موفقیت ساخته شد!</b>\n"
        f"تعداد <b>{total_synced}</b> کلمه کلیدی از مقالات سایت استخراج و در دیتابیس ربات (SEO Ledger) قفل شد.",
        parse_mode="HTML"
    )
