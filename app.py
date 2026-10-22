import logging
import os
import re
import io
import asyncio
import aiohttp
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==========================================
# KONFIGURASI
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
AUTHORIZED_ID = 8227396820

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Penyimpanan memori
user_files = {}
user_results = {}

# ==========================================
# WEB SERVER (Wajib untuk Render Free Tier)
# ==========================================
async def health_check(request):
    return web.Response(text="Bot is running perfectly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server listening on port {port}")

# ==========================================
# OTORISASI
# ==========================================
def is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == AUTHORIZED_ID

# ==========================================
# MENU UTAMA
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⚠️ هذا البوت خاص. غير مصرح لك.")
        return

    keyboard = [
        [KeyboardButton("🔍 فحص BIN"), KeyboardButton("📁 رفع ملف خام")],
        [KeyboardButton("📥 تصدير النتائج"), KeyboardButton("🗑 مسح البيانات")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "أهلاً بك. البوت جاهز.\n\n"
        "1️⃣ أرسل BIN (6 أرقام) أو أسطر كاملة لفحصها تلقائياً.\n"
        "2️⃣ ارفع ملف TXT للبحث عن أرقام كاملة (15-19 رقم) بداخله.\n"
        "3️⃣ استخدم الأزرار للتصدير والمسح.",
        reply_markup=markup
    )

# ==========================================
# 1. BIN LOOKUP ENGINE
# ==========================================
async def fetch_bin_info(session: aiohttp.ClientSession, bin_number: str) -> str:
    try:
        async with session.get(f"https://lookup.binlist.net/{bin_number}", headers={"Accept-Version": "3"}, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                brand = data.get("scheme", "UNKNOWN").upper()
                country = data.get("country", {}).get("name", "UNKNOWN").upper()
                ctype = data.get("type", "UNKNOWN").upper()
                level = data.get("brand", "UNKNOWN").upper()
                bank = data.get("bank", {}).get("name", "UNKNOWN").upper()
                return f"{brand} | {country} | {ctype} | {level} | {bank}"
            return "NOT_FOUND | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN"
    except Exception as e:
        logger.error(f"API Error for {bin_number}: {e}")
        return "API_ERROR | RETRY | UNKNOWN | UNKNOWN | UNKNOWN"

async def handle_bin_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if not lines:
        return

    await update.message.reply_text("⏳ جاري جلب معلومات BIN...")
    results = []

    # Menggunakan aiohttp untuk request paralel (cepat & tidak nge-hang)
    async with aiohttp.ClientSession() as session:
        tasks = []
        for line in lines:
            match = re.search(r'\b(\d{6,8})', line)
            if match:
                bin_num = match.group(1)
                tasks.append((line, fetch_bin_info(session, bin_num)))
            else:
                results.append(f"{line} | [BIN Not Found]")
        
        # Eksekusi paralel dengan batas aman
        for line, task in tasks:
            bin_info = await task
            results.append(f"{line} | {bin_info}")

    user_results[update.effective_user.id] = results
    reply = "✅ **نتائج الفحص:**\n\n" + "\n".join(results)
    
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i+4000], parse_mode="Markdown")

# ==========================================
# 2. FILE UPLOAD & RAW SEARCH ENGINE
# ==========================================
async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith('.txt'):
        await update.message.reply_text("❌ فقط ملفات .txt مسموحة.")
        return

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    text = raw.decode('utf-8', errors='replace')
    
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    user_files[update.effective_user.id] = lines

    await update.message.reply_text(
        f"✅ تم حفظ الملف في الذاكرة.\n"
        f"📊 الأسطر: {len(lines)}\n\n"
        f"أرسل الآن أرقام البطاقات (15-19 رقم) للبحث عنها."
    )

async def handle_raw_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    card_numbers = list(set(re.findall(r'\b\d{15,19}\b', text))) # Unique cards only
    
    if not card_numbers:
        await update.message.reply_text("⚠️ لم أجد أرقام بطاقات كاملة (15-19 رقم) في رسالتك.")
        return

    file_lines = user_files[update.effective_user.id]
    found_results = []

    await update.message.reply_text(f"🔎 جاري البحث عن {len(card_numbers)} بطاقة داخل الملف...")
    
    for card in card_numbers:
        matches = [line for line in file_lines if card in line]
        if matches:
            found_results.extend(matches)
        else:
            found_results.append(f"{card} | NOT_FOUND_IN_FILE")

    user_results[update.effective_user.id] = found_results
    reply = "✅ **نتائج البحث في الملف:**\n\n" + "\n".join(found_results)
    
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i+4000])

# ==========================================
# 3. EXPORT & CLEAR
# ==========================================
async def export_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_results or not user_results[user_id]:
        await update.message.reply_text("⚠️ لا توجد نتائج للتصدير.")
        return

    output = io.BytesIO()
    output.write("\n".join(user_results[user_id]).encode('utf-8'))
    output.seek(0)

    await update.message.reply_document(
        document=output,
        filename="ALPHA_Results.txt",
        caption="📥 تم تصدير النتائج بنجاح."
    )

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_files.pop(user_id, None)
    user_results.pop(user_id, None)
    await update.message.reply_text("🗑 تم مسح الملفات والنتائج.")

# ==========================================
# ROUTER (Mendeteksi otomatis apa yang dikirim)
# ==========================================
async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    text = update.message.text
    user_id = update.effective_user.id

    if text == "🔍 فحص BIN":
        await update.message.reply_text("📝 أرسل الأسطر أو BIN الآن.")
    elif text == "📁 رفع ملف خام":
        await update.message.reply_text("📤 أرسل ملف الـ TXT الآن.")
    elif text == "📥 تصدير النتائج":
        await export_results(update, context)
    elif text == "🗑 مسح البيانات":
        await clear_data(update, context)
    else:
        # AUTO-ROUTING PINTAR
        # Jika ada file di memori DAN pesan berisi angka 15-19 digit -> Raw Search
        has_full_card = re.search(r'\b\d{15,19}\b', text)
        if user_id in user_files and has_full_card:
            await handle_raw_search(update, context)
        # Jika bukan, anggap saja minta BIN Lookup
        else:
            await handle_bin_lookup(update, context)

# ==========================================
# MAIN RUNNER
# ==========================================
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.TXT, handle_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    # Start Web Server for Render
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()