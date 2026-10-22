import logging
import os
import re
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(name)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set. Set it in .env or Railway env vars.")

WAITING_QUERY = 1

# {user_id: [baris...]}
user_lines: dict[int, list[str]] = {}

CARD_NO_RE = re.compile(r"\b\d{12,19}\b")


def parse_queries(text: str) -> list[str]:
    """
    Terima input berbilang baris / dipisah koma / spasi.
    Ambil nombor 12-19 digit sahaja sebagai nombor kad.
    Urutan dikekalkan, duplikat input dibuang sekali sahaja.
    """
    candidates = CARD_NO_RE.findall(text)
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [["📤 رفع ملف TXT"], ["🔍 بحث"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "أهلاً.\n"
        "1) ارفع ملف TXT\n"
        "2) اضغط 🔍 بحث وأرسل الأرقام (سطر لكل رقم أو مفصولة بفواصل).\n"
        "سأرجع السطر الأصلي كما هو بدون تعديل.",
        reply_markup=markup,
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("فقط ملفات .txt مسموحة.")
        return

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    text = raw.decode("utf-8", errors="replace")
    # Simpan setiap baris PERSIS seperti asal (hanya buang \r di hujung)
    lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]

    user_id = update.effective_user.id
    user_lines[user_id] = lines

    await update.message.reply_text(
        f"تم تحميل الملف. عدد الأسطر: {len(lines)}\n"
        "اضغط 🔍 بحث الآن."
    )
    logger.info("User %s uploaded file with %d lines", user_id, len(lines))


async def ask_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in user_lines:
        await update.message.reply_text("ما رفعت ملف بعد. ارفع TXT أولاً.")
        return ConversationHandler.END
    await update.message.reply_text(
        "أرسل الأرقام. مثال:\n"
        "9441177200190555\n"
        "9491034350234395\n"
        "9792860211834833"
    )
    return WAITING_QUERY


def find_matches(lines: list[str], queries: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """
    Untuk setiap query, cari baris yang MENGANDUNGI query sebagai substring penuh.
    Pulangkan:
      - matches: {query: [baris...]} ikut urutan query asal
      - missing: [query...] yang tak jumpa
    Baris asal tidak diubah.
    """
    matches: dict[str, list[str]] = {}
    missing: list[str] = []
    for q in queries:
        hits = [ln for ln in lines if q in ln]
        if hits:
            matches[q] = hits
        else:
            missing.append(q)
    return matches, missing


def build_reply(queries: list[str], matches: dict[str, list[str]], missing: list[str]) -> str:
    """
    Susun output ikut urutan query asal.
    Setiap query: header + baris asal penuh.
    Missing dilaporkan di hujung sekali.
    Tiada edit, tiada dedup, tiada sorting semula.
    """
    parts: list[str] = []
    for q in queries:
        if q in matches:
            parts.append(f"=== {q} ===")
            parts.extend(matches[q])
            parts.append("")
    if missing:
        parts.append("=== بدون نتيجة ===")
        parts.extend(missing)
    return "\n".join(parts).strip()

def chunk_text(text: str, size: int = 3900) -> list[str]:
    """
    Telegram had 4096 char/message. Potong atas sempadan baris bila boleh.
    """
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > size and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def run_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    lines = user_lines.get(user_id, [])
    if not lines:
        await update.message.reply_text("ما في ملف محمّل. ارفع TXT أولاً.")
        return ConversationHandler.END

    queries = parse_queries(update.message.text or "")
    if not queries:
        await update.message.reply_text(
            "ما لقيت أرقام صالحة (12-19 رقم). حاول مرة ثانية."
        )
        return WAITING_QUERY

    matches, missing = find_matches(lines, queries)
    reply = build_reply(queries, matches, missing)

    if not reply:
        await update.message.reply_text("ما لقيت أي نتيجة.")
        return ConversationHandler.END

    for chunk in chunk_text(reply):
        await update.message.reply_text(chunk)

    logger.info(
        "User %s searched %d queries, %d hit, %d missing",
        user_id, len(queries), len(matches), len(missing),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("استخدم الأزرار: 📤 رفع ملف TXT أو 🔍 بحث.")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 بحث$"), ask_query)],
        states={
            WAITING_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, run_search),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.TXT, handle_document))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if name == "main":
    main()