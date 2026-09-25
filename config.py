import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# کلیدهای ووکامرس
WC_URL = os.getenv("WC_URL")
WC_CONSUMER_KEY = os.getenv("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.getenv("WC_CONSUMER_SECRET")
WC_WEBHOOK_SECRET = os.getenv("WC_WEBHOOK_SECRET")

# کلیدهای وردپرس (برای انتشار مقاله و تصویر)
WP_USER = os.getenv("WP_USER")
WP_APP_PASS = os.getenv("WP_APP_PASS")

# دیتابیس سوپابیس
DATABASE_URL = os.getenv("DATABASE_URL")

# بررسی وجود متغیرهای حیاتی
if not all([
    BOT_TOKEN, ADMIN_ID, WC_URL, 
    WC_CONSUMER_KEY, WC_CONSUMER_SECRET, WC_WEBHOOK_SECRET, 
    WP_USER, WP_APP_PASS, DATABASE_URL
]):
    raise ValueError("❌ Missing environment variables! Check Render settings or .env file.")

ADMIN_ID = int(ADMIN_ID)
WC_URL = WC_URL.rstrip("/")
