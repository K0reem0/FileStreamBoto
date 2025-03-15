from hydrogram import filters
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from secrets import token_hex
from bot import TelegramBot
from bot.config import Telegram
from bot.modules.decorators import verify_user
import subprocess
import os

DOWNLOAD_PATH = "downloads"  # مجلد لحفظ الفيديوهات مؤقتًا
os.makedirs(DOWNLOAD_PATH, exist_ok=True)  # إنشاء المجلد إن لم يكن موجودًا

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
        subprocess.run(
            ["yt-dlp", "-o", output_path, "-f", "best", video_url],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except subprocess.CalledProcessError as e:
        return await msg.reply(f"❌ فشل تحميل الفيديو:\n{e.stderr}")

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

    # حذف الفيديو من التخزين المؤقت بعد الإرسال
    os.remove(output_path)
