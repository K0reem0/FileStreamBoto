from hydrogram import filters
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from secrets import token_hex
import os
import yt_dlp
import asyncio
from playwright.async_api import async_playwright
import shutil
import time

DOWNLOAD_PATH = "downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

async def scrape_video_url(page_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            video_urls = []
            
            def handle_response(response):
                url = response.url
                if url and any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', '.m3u8']):
                    video_urls.append(url)
            
            page.on('response', handle_response)
            
            await page.goto(page_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)  # انتظار إضافي

            # محاولة الحصول على أفضل مصدر للفيديو
            video_src = await page.evaluate('''() => {
                // 1. البحث عن عنصر فيديو مباشر
                const videoElement = document.querySelector('video');
                if (videoElement?.src) return videoElement.src;
                
                // 2. البحث عن عناصر مصدر داخل الفيديو
                const sources = videoElement?.querySelectorAll('source');
                if (sources) {
                    for (const source of sources) {
                        if (source.src && source.type.includes('video')) {
                            return source.src;
                        }
                    }
                }
                
                // 3. البحث عن iframes تحتوي على فيديو
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    if (iframe.src && iframe.src.match(/video|player|embed/)) {
                        return iframe.src;
                    }
                }
                
                // 4. البحث عن روابط فيديو في data attributes
                const potentialElements = document.querySelectorAll('[data-video-src], [data-src]');
                for (const el of potentialElements) {
                    const src = el.getAttribute('data-video-src') || el.getAttribute('data-src');
                    if (src && src.match(/\.(mp4|webm|mov)/)) {
                        return src;
                    }
                }
                
                return null;
            }''')

            # الجمع بين جميع المصادر
            all_urls = list(set([url for url in [video_src] + video_urls if url]))
            if not all_urls:
                raise Exception('لم يتم العثور على روابط فيديو في الصفحة')
            
            # اختيار أفضل جودة (أطول رابط عادةً يكون الأعلى جودة)
            best_url = max(all_urls, key=lambda x: len(x))
            return best_url

        except Exception as e:
            raise Exception(f'فشل استخراج رابط الفيديو: {str(e)}')
        finally:
            await context.close()
            await browser.close()

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
            await asyncio.to_thread(ydl.download, [url])
        return True
    except Exception as e:
        print(f'❌ فشل تحميل الفيديو باستخدام yt-dlp: {str(e)}')
        return False

async def download_video_directly(url, output_path):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_timeout(3000)
            
            # تنزيل الملف مباشرة
            async with page.expect_download() as download_info:
                await page.evaluate('''() => {
                    const video = document.querySelector('video');
                    if (video) {
                        const a = document.createElement('a');
                        a.href = video.src;
                        a.download = 'video.mp4';
                        a.click();
                    }
                }''')
            
            download = await download_info.value
            await download.save_as(output_path)
            return True
            
    except Exception as e:
        print(f'❌ فشل التحميل المباشر: {str(e)}')
        return False
    finally:
        await context.close()
        await browser.close()

@TelegramBot.on_message(filters.private & filters.text)
async def handle_video_link(_, msg: Message):
    video_url = msg.text.strip()

    if not video_url.startswith(("http://", "https://")):
        return await msg.reply("❌ يرجى إرسال رابط فيديو صالح!")

    secret_code = token_hex(8)
    output_filename = f"{secret_code}.mp4"
    output_path = os.path.join(DOWNLOAD_PATH, output_filename)

    waiting_msg = await msg.reply("⏳ جاري محاولة التحميل باستخدام yt-dlp...")

    try:
        # المحاولة الأولى باستخدام yt-dlp
        success = await download_with_yt_dlp(video_url, output_path)
        
        if not success:
            await waiting_msg.edit_text("⚠️ فشل yt-dlp، جاري محاولة استخراج الفيديو مباشرة...")
            video_url = await scrape_video_url(video_url)
            await waiting_msg.edit_text("🔗 تم استخراج رابط الفيديو، جاري التحميل الآن...")
            success = await download_video_directly(video_url, output_path)
            
            if not success:
                raise Exception("فشل جميع محاولات التحميل")

        # إرسال الفيديو للمستخدم
        await msg.reply_video(
            video=output_path,
            caption="✅ تم تحميل الفيديو بنجاح!",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 إعادة التحميل", callback_data=f"reload_{secret_code}"),
                        InlineKeyboardButton("🗑 حذف الفيديو", callback_data=f"delete_{secret_code}")
                    ]
                ]
            )
        )

    except Exception as e:
        await waiting_msg.edit_text(f"❌ فشل تحميل الفيديو:\n{str(e)}")
    finally:
        await waiting_msg.delete()
        if os.path.exists(output_path):
            os.remove(output_path)

@TelegramBot.on_callback_query(filters.regex(r"^reload_"))
async def handle_reload(_, query):
    secret_code = query.data.split("_")[1]
    await query.answer("⏳ سيتم إعادة تحميل الفيديو قريباً...", show_alert=True)

@TelegramBot.on_callback_query(filters.regex(r"^delete_"))
async def handle_delete(_, query):
    await query.message.delete()
    await query.answer("🗑 تم حذف الفيديو", show_alert=True)
