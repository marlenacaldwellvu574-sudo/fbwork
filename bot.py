import os
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from openpyxl import Workbook, load_workbook

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

EXCEL_FILE = "data.xlsx"

WAIT_UID, WAIT_DATA = 1, 2

logging.basicConfig(level=logging.INFO)

# ---------- INIT EXCEL ----------
def init_excel():
    if not os.path.exists(EXCEL_FILE):
        wb = Workbook()
        ws = wb.active
        ws.append(["UID", "PASSWORD", "DATA"])
        wb.save(EXCEL_FILE)

init_excel()

# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "🚀 Bot Ready\n\n"
        "/add - Add entry\n"
        "/setpw - Set global password\n"
        "/dl - Download Excel\n"
        "/dlt - Reset Excel"
    )

# ---------- SET PASSWORD ----------
async def setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    pw = update.message.text.replace("/setpw", "").strip()

    if not pw:
        await update.message.reply_text("Send like: /setpw your_password")
        return

    with open("password.txt", "w") as f:
        f.write(pw)

    await update.message.reply_text("✅ Global password saved")

# ---------- ADD FLOW ----------
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text("Send UID:")
    return WAIT_UID

async def get_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["uid"] = update.message.text.strip()
    await update.message.reply_text("Send DATA:")
    return WAIT_DATA

async def get_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["uid"]
    data = update.message.text.strip()

    # load password
    pw = ""
    if os.path.exists("password.txt"):
        with open("password.txt", "r") as f:
            pw = f.read().strip()

    wb = load_workbook(EXCEL_FILE)
    ws = wb.active

    ws.append([uid, pw, data])
    wb.save(EXCEL_FILE)

    await update.message.reply_text("✅ Saved successfully")
    return ConversationHandler.END

# ---------- DOWNLOAD ----------
async def dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"))

# ---------- RESET ----------
async def dlt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    wb = Workbook()
    ws = wb.active
    ws.append(["UID", "PASSWORD", "DATA"])
    wb.save(EXCEL_FILE)

    await update.message.reply_text("🗑 Excel reset done")

# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add)],
        states={
            WAIT_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_uid)],
            WAIT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_data)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setpw", setpw))
    app.add_handler(conv)
    app.add_handler(CommandHandler("dl", dl))
    app.add_handler(CommandHandler("dlt", dlt))

    app.run_polling()

if __name__ == "__main__":
    main()
