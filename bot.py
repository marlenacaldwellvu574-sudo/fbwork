import os
import logging
import asyncio
import tempfile
import shutil
import time
import json

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


# ───────────────── CONFIG ─────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN")
_admin_id = os.getenv("ADMIN_ID")

if not BOT_TOKEN or not _admin_id:
    raise SystemExit("Missing env variables")

ADMIN_ID = int(_admin_id)

PASSWORD_FILE = "fb_password.txt"

WAITING_PW, WAITING_PHONE = 1, 2

logging.basicConfig(level=logging.INFO)


# ───────────────── SELENIUM FIX ─────────────────

def get_driver():
    # IMPORTANT FIX: persistent profile instead of temp chaos
    profile_dir = os.path.abspath("chrome_profile")

    options = Options()

    # ❌ FIX: DO NOT use --app mode (this caused Google/blank page issue)

    options.add_argument("--headless=new")

    # stability flags
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # reduce bot detection issues
    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except:
        pass

    return driver, profile_dir


# ───────────────── LOGIN ─────────────────

def do_login(phone, password):
    driver, profile_dir = get_driver()

    try:
        driver.get("https://m.facebook.com/login/")
        wait = WebDriverWait(driver, 20)

        # wait for fields
        email = wait.until(
            EC.presence_of_element_located((By.NAME, "email"))
        )

        email.clear()
        email.send_keys(phone)

        pwd = driver.find_element(By.NAME, "pass")
        pwd.clear()
        pwd.send_keys(password)

        driver.find_element(By.NAME, "login").click()

        # wait for redirect
        time.sleep(5)

        url = driver.current_url
        logging.info(f"URL after login: {url}")

        # checkpoint detection
        if "checkpoint" in url:
            return None, "Checkpoint / verification required"

        cookies = driver.get_cookies()
        names = [c["name"] for c in cookies]

        if "c_user" not in names:
            return None, "Login failed (no session found)"

        return cookies, None

    except Exception as e:
        driver.save_screenshot("debug.png")
        return None, f"Error: {str(e)}"

    finally:
        try:
            driver.quit()
        except:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


# ───────────────── TELEGRAM BOT ─────────────────

def get_pw():
    if os.path.exists(PASSWORD_FILE):
        return open(PASSWORD_FILE).read().strip()
    return None


def save_pw(pw):
    with open(PASSWORD_FILE, "w") as f:
        f.write(pw)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "Bot Running\nUse /setpw /add"
    )


# ───────────────── PASSWORD ─────────────────

async def setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send password")
    return WAITING_PW


async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_pw(update.message.text.strip())
    await update.message.reply_text("Saved")
    return ConversationHandler.END


# ───────────────── LOGIN FLOW ─────────────────

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send phone")
    return WAITING_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    pw = get_pw()

    msg = await update.message.reply_text("Logging in...")

    # FIX: prevent freezing bot
    cookies, error = await asyncio.to_thread(do_login, phone, pw)

    if error:
        await msg.edit_text(f"Failed: {error}")
        return ConversationHandler.END

    await msg.edit_text(f"Success! Cookies: {len(cookies)}")
    return ConversationHandler.END


# ───────────────── MAIN ─────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", setpw)],
        states={WAITING_PW: [MessageHandler(filters.TEXT, save_password)]},
        fallbacks=[]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add)],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT, handle_phone)]},
        fallbacks=[]
    ))

    app.run_polling()


if __name__ == "__main__":
    main()
