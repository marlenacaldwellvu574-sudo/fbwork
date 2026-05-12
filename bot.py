import os
import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from openpyxl import Workbook, load_workbook
import time
import json
import tempfile
import shutil

BOT_TOKEN = os.getenv("BOT_TOKEN")
_admin_id  = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN environment variable is not set.")
if not _admin_id:
    raise SystemExit("ERROR: ADMIN_ID environment variable is not set.")
try:
    ADMIN_ID = int(_admin_id)
except ValueError:
    raise SystemExit(f"ERROR: ADMIN_ID must be a number, got: {_admin_id!r}")

PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE    = "cookies.xlsx"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

WAITING_PW    = 1
WAITING_PHONE = 2


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_saved_password():
    if os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, "r") as f:
            return f.read().strip()
    return None

def save_password(pw):
    with open(PASSWORD_FILE, "w") as f:
        f.write(pw)

def save_cookies_to_excel(phone, cookies):
    cookies_str = json.dumps(cookies)

    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Cookies"
        ws["A1"] = "Row"
        ws["B1"] = "Phone"
        ws["C1"] = "Cookies"

    next_row = ws.max_row + 1
    ws[f"A{next_row}"] = next_row - 1
    ws[f"B{next_row}"] = phone
    ws[f"C{next_row}"] = cookies_str
    wb.save(EXCEL_FILE)


def get_driver():
    profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    options = Options()

    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    return driver, profile_dir


def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()

    try:
        driver.get("https://www.facebook.com")
        time.sleep(3)

        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "email"))
        )

        email_field.clear()
        email_field.send_keys(phone)

        pass_field = driver.find_element(By.ID, "pass")
        pass_field.clear()
        pass_field.send_keys(password)

        driver.find_element(By.NAME, "login").click()
        time.sleep(6)

        current_url = driver.current_url

        if "checkpoint" in current_url:
            return None, "Checkpoint detected"

        cookies = driver.get_cookies()

        cookie_names = [c["name"] for c in cookies]

        if "c_user" not in cookie_names and "xs" not in cookie_names:
            return None, "Login failed - session cookies not found"

        return cookies, None

    except Exception as e:
        return None, f"Error: {str(e)}"

    finally:
        try:
            driver.quit()
        except:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    pw = get_saved_password()
    pw_status = "✅ Password saved" if pw else "❌ No password saved"

    await update.message.reply_text(
        f"Facebook Bot\n\n{pw_status}\n\n/use /setpw /add /dl /dlt"
    )


# ─────────────────────────────────────────────
# SET PASSWORD
# ─────────────────────────────────────────────

async def cmd_setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text("Send password")
    return WAITING_PW


async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_password(update.message.text.strip())
    await update.message.reply_text("Saved")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# ADD LOGIN (FIXED ASYNC BLOCKING)
# ─────────────────────────────────────────────

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text("Send phone")
    return WAITING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    pw = get_saved_password()

    msg = await update.message.reply_text("Logging in...")

    # FIX: prevent blocking bot
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"Failed: {error}")
        return ConversationHandler.END

    save_cookies_to_excel(phone, cookies)

    await msg.edit_text(f"Success: {len(cookies)} cookies saved")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# DOWNLOAD
# ─────────────────────────────────────────────

async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        with open(EXCEL_FILE, "rb") as f:
            await update.message.reply_document(f)


# ─────────────────────────────────────────────
# DELETE FILE
# ─────────────────────────────────────────────

async def cmd_dlt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        os.remove(EXCEL_FILE)
        await update.message.reply_text("Deleted")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    setpw_conv = ConversationHandler(
        entry_points=[CommandHandler("setpw", cmd_setpw)],
        states={WAITING_PW: [MessageHandler(filters.TEXT, receive_password)]},
        fallbacks=[]
    )

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT, receive_phone)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(setpw_conv)
    app.add_handler(add_conv)
    app.add_handler(CommandHandler("dl", cmd_dl))
    app.add_handler(CommandHandler("dlt", cmd_dlt))

    app.run_polling()


if __name__ == "__main__":
    main()
