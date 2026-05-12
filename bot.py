import os
import logging
import asyncio
import json
import time
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

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
_admin_id = os.getenv("ADMIN_ID")
ADMIN_ID = int(_admin_id) if _admin_id else 0

PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE = "cookies.xlsx"
DEBUG_PHOTO = "debug.png"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_PW, WAITING_PHONE = 1, 2


# --- BROWSER ENGINE ---

def get_driver():
    profile_dir = os.path.abspath("chrome_profile")
    options = Options()

    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )

    driver = webdriver.Chrome(options=options)

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass

    return driver, profile_dir


def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 25)

    try:
        # Step 1: Navigate to login page
        driver.get("https://m.facebook.com/login/")
        time.sleep(5)

        # Force navigation if stuck
        if "facebook.com" not in driver.current_url:
            driver.execute_script("window.location.replace('https://m.facebook.com/login/');")
            time.sleep(6)

        # Step 2: Dismiss cookie/consent banners
        driver.execute_script("""
            var btns = document.querySelectorAll('button, div[role="button"], a');
            btns.forEach(function(b) {
                var t = (b.innerText || '').toLowerCase();
                if (t.includes('accept') || t.includes('allow') || t.includes('only allow')) {
                    b.click();
                }
            });
        """)
        time.sleep(2)

        # Step 3: Wait for the email field to appear
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Login form not found. See screenshot."

        # Step 4: Inject credentials using React-compatible synthetic events
        # This properly triggers React's onChange handlers unlike plain .value assignment
        driver.execute_script("""
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;

            var emailField = document.getElementsByName('email')[0];
            var passField  = document.getElementsByName('pass')[0];

            nativeInputValueSetter.call(emailField, arguments[0]);
            emailField.dispatchEvent(new Event('input',  { bubbles: true }));
            emailField.dispatchEvent(new Event('change', { bubbles: true }));

            nativeInputValueSetter.call(passField, arguments[1]);
            passField.dispatchEvent(new Event('input',  { bubbles: true }));
            passField.dispatchEvent(new Event('change', { bubbles: true }));
        """, phone, password)

        time.sleep(1)

        # Step 5: Click the login button (try multiple selectors)
        clicked = driver.execute_script("""
            var btn = document.querySelector('[data-sigil="m_login_button"]')
                   || document.querySelector('button[type="submit"]')
                   || document.getElementsByName('login')[0];
            if (btn) {
                btn.click();
                return true;
            }
            return false;
        """)

        if not clicked:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Could not find login button. See screenshot."

        # Step 6: Poll for successful session cookie (longer initial wait)
        time.sleep(4)
        for _ in range(15):
            time.sleep(2)
            current_url = driver.current_url
            cookies = {c['name']: c['value'] for c in driver.get_cookies()}

            if 'c_user' in cookies:
                logger.info(f"Login successful for {phone}")
                return driver.get_cookies(), None

            if "checkpoint" in current_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Checkpoint / 2FA detected. Manual action required."

            if "login" not in current_url and "facebook.com" in current_url:
                # Redirected away from login — may still be loading session
                time.sleep(3)
                cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                if 'c_user' in cookies:
                    return driver.get_cookies(), None

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Timed out waiting for session. Wrong password or blocked."

    except Exception as e:
        try:
            driver.save_screenshot(DEBUG_PHOTO)
        except Exception:
            pass
        return None, f"Runtime Error: {str(e)}"

    finally:
        driver.quit()


# --- EXCEL STORAGE ---

def save_cookies_to_excel(phone, cookies):
    c_json = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(["#", "Phone", "Cookies"])

    row_num = ws.max_row  # use current max_row as the ID
    ws.append([row_num, phone, c_json])
    wb.save(EXCEL_FILE)


# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "✅ Bot Ready.\n\n"
        "/setpw — Set Facebook password\n"
        "/add    — Add account (enter phone)\n"
        "/dl     — Download cookies Excel"
    )


async def setpw_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("Send the Facebook password:")
    return WAITING_PW


async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw = update.message.text.strip()
    with open(PASSWORD_FILE, "w") as f:
        f.write(pw)
    await update.message.reply_text("✅ Password saved.")
    return ConversationHandler.END


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📱 Send the phone number (e.g. +51928065251):")
    return WAITING_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()

    if not os.path.exists(PASSWORD_FILE):
        await update.message.reply_text("❌ No password set. Use /setpw first.")
        return ConversationHandler.END

    pw = open(PASSWORD_FILE).read().strip()
    if not pw:
        await update.message.reply_text("❌ Password file is empty. Use /setpw again.")
        return ConversationHandler.END

    msg = await update.message.reply_text(f"⏳ Logging in as {phone} ...")

    # Run blocking Selenium login in a thread
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        if os.path.exists(DEBUG_PHOTO):
            await update.message.reply_photo(
                photo=open(DEBUG_PHOTO, "rb"),
                caption="🔍 Debug screenshot"
            )
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! {len(cookies)} cookies saved for {phone}.")

    return ConversationHandler.END


async def dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(
            document=open(EXCEL_FILE, "rb"),
            filename=EXCEL_FILE,
            caption="📊 Cookies Excel"
        )
    else:
        await update.message.reply_text("❌ No Excel file found yet.")


# --- MAIN ---

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set.")

    app = Application.builder().token(BOT_TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start))

    # /dl
    app.add_handler(CommandHandler("dl", dl))

    # /setpw conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", setpw_entry)],
        states={
            WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password)]
        },
        fallbacks=[]
    ))

    # /add conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_entry)],
        states={
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)]
        },
        fallbacks=[]
    ))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
