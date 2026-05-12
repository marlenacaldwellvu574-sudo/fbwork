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

WAIT_UID, WAIT_DATA, WAIT_PW = 1, 2, 3

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
        "🚀 Bot Ready\n"
        "/add - Add entry\n"
        "/setpw - Set password\n"
        "/dl - Download Excel\n"
        "/dlt - Reset file"
    )

# ---------- ADD ----------
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Send UID:")
    return WAIT_UID

async def get_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["uid"] = update.message.text.strip()
    await update.message.reply_text("Now send DATA:")
    return WAIT_DATA

async def get_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["data"] = update.message.text.strip()

    await update.message.reply_text("Send password (or type skip):")
    return WAIT_PW

async def get_pw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw = update.message.text.strip()

    uid = context.user_data["uid"]
    data = context.user_data["data"]

    wb = load_workbook(EXCEL_FILE)
    ws = wb.active

    ws.append([uid, pw, data])
    wb.save(EXCEL_FILE)

    await update.message.reply_text("✅ Saved to Excel")
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
            WAIT_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pw)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("dl", dl))
    app.add_handler(CommandHandler("dlt", dlt))

    app.run_polling()

if __name__ == "__main__":
    main()
