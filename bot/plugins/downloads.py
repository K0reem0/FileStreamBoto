from hydrogram import filters
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from secrets import token_hex
from bot import TelegramBot
from bot.config import Telegram
from bot.modules.decorators import verify_user
import subprocess
import os
import yt_dlp
import asyncio
from playwright.async_api import async_playwright
import shutil

DOWNLOAD_PATH = "downloads"  # مجلد لحفظ الفيديوهات مؤقتًا
os.makedirs(DOWNLOAD_PATH, exist_ok=True)  # إنشاء المجلد إن لم يكن موجودًا

# تنظيف الملفات القديمة كل 5 دقائق
async def cleanup_old_files():
    while True:
        await asyncio.sleep(5 * 60)  # كل 5 دقائق
        try:
            now = time.time()
            five_minutes = 5 * 60
            for filename in os.listdir(DOWNLOAD_PATH):
                filepath = os.path.join(DOWNLOAD_PATH, filename)
                if os.path.isfile(filepath):
                    stat = os.stat(filepath)
                    if now - stat.st_mtime > five_minutes:
                        os.remove(filepath)
                        print(f"🗑 Deleted old file: {filepath}")
        except Exception as e:
            print(f"⚠️ Error clearing old files: {e}")

# بدء عملية التنظيف في الخلفية
asyncio.create_task(cleanup_old_files())

# استخراج رابط الفيديو باستخدام Playwright
async def scrape_video_url(page_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            video_urls = []
            
            def handle_response(response):
                url = response.url
                if url and any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', '.m3u8']):
                    video_urls.append(url)
            
            page.on('response', handle_response)
            
            await page.goto(page_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)  # انتظار إضافي

            # البحث في عناصر الفيديو
            element_video_src = await page.evaluate('''() => {
                const videoElement = document.querySelector('video');
                if (videoElement?.src) return videoElement.src;
                
                const sourceElement = videoElement?.querySelector('source');
                if (sourceElement?.src) return sourceElement.src;
                
                const iframe = document.querySelector('iframe[src*="video"], iframe[src*="player"]');
                if (iframe?.src) return iframe.src;
                
                return null;
            }''')

            await browser.close()

            # الجمع بين جميع المصادر
            all_urls = list(set([url for url in [element_video_src] + video_urls if url]))
            if not all_urls:
                raise Exception('No video URLs found')
            
            return sorted(all_urls, key=lambda x: len(x), reverse=True)[0]  # أعلى جودة
        except Exception as e:
            await browser.close()
            raise e

# تحميل الفيديو بأعلى جودة باستخدام yt-dlp
async def download_with_yt_dlp(url, output_path):
    try:
        ydl_opts = {
            'outtmpl': output_path,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'prefer_free_formats': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f'❌ yt-dlp error: {str(e)}')
        return False

@TelegramBot.on_message(filters.private & filters.text)
@verify_user
async def handle_video_link(_, msg: Message):
    video_url = msg.text.strip()

    # التحقق من أن الرابط صالح
    if not video_url.startswith(("http://", "https://")):
        return await msg.reply("❌ يرجى إرسال رابط فيديو صالح!")

    sender_id = msg.from_user.id
    secret_code = token_hex(8)  # رمز سري لحماية الرابط
    output_filename = f"{secret_code}.mp4"
    output_path = os.path.join(DOWNLOAD_PATH, output_filename)

    # تنزيل الفيديو باستخدام yt-dlp
    waiting_msg = await msg.reply("⏳ جاري تحميل الفيديو، يرجى الانتظار...")

    try:
        download_success = await download_with_yt_dlp(video_url, output_path)
        
        if not download_success:
            await waiting_msg.edit_text("⚠️ جاري محاولة استخراج الفيديو مباشرة...")
            try:
                extracted_url = await scrape_video_url(video_url)
                print(f'🔗 Extracted URL: {extracted_url}')
                download_success = await download_with_yt_dlp(extracted_url, output_path)
                if not download_success:
                    raise Exception('Download failed after extraction')
            except Exception as extract_error:
                print(f'❌ Extraction failed: {extract_error}')
                raise Exception('All methods failed')

        # إرسال الفيديو إلى المستخدم مع الأزرار
        video_msg = await msg.reply_video(
            video=output_path,
            caption="✅ تم تحميل الفيديو بنجاح!\n\nاختر أحد الخيارات:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 إعادة التحميل", callback_data=f"reload_{secret_code}"),
                        InlineKeyboardButton("🗑 حذف الفيديو", callback_data=f"delete_{secret_code}")
                    ]
                ]
            )
        )

        # حذف رسالة "جاري التحميل..."
        await waiting_msg.delete()

    except Exception as e:
        await waiting_msg.edit_text(f"❌ فشل تحميل الفيديو:\n{str(e)}")
    finally:
        # حذف الفيديو من التخزين المؤقت بعد الإرسال
        if os.path.exists(output_path):
            os.remove(output_path)

@TelegramBot.on_callback_query(filters.regex(r"^reload_"))
async def handle_reload(_, query):
    secret_code = query.data.split("_")[1]
    output_path = os.path.join(DOWNLOAD_PATH, f"{secret_code}.mp4")
    
    await query.answer("⏳ جاري إعادة تحميل الفيديو...")
    
    # هنا يمكنك إعادة تنفيذ عملية التحميل
    # سأترك التطبيق لك حسب احتياجاتك

@TelegramBot.on_callback_query(filters.regex(r"^delete_"))
async def handle_delete(_, query):
    secret_code = query.data.split("_")[1]
    output_path = os.path.join(DOWNLOAD_PATH, f"{secret_code}.mp4")
    
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
        await query.message.delete()
        await query.answer("🗑 تم حذف الفيديو بنجاح!")
    except Exception as e:
        await query.answer(f"❌ فشل حذف الفيديو: {str(e)}")
