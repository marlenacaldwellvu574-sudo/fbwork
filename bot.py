import os
import logging
import asyncio
import json
import shutil
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

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN")
_admin_id  = os.getenv("ADMIN_ID")
ADMIN_ID   = int(_admin_id) if _admin_id else 0

PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE    = "cookies.xlsx"
DEBUG_PHOTO   = "debug.png"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_PW, WAITING_PHONE = 1, 2


# ── BROWSER ───────────────────────────────────────────────────────────────────

def get_driver():
    """Create a clean Chrome driver with no previous session state."""
    profile_dir = os.path.abspath("chrome_profile")

    # Wipe old profile so Chrome never restores a previous session / Google page
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)
    os.makedirs(profile_dir, exist_ok=True)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )

    driver = webdriver.Chrome(options=options)

    # Hide the automation flag from JavaScript
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass

    return driver


def do_facebook_login(phone: str, password: str):
    """
    Open m.facebook.com/login, fill in credentials and return all cookies.
    Returns: (list_of_cookie_dicts, None)  on success
             (None, error_string)           on failure
    """
    driver = None  # must be initialised here so finally block is safe
    try:
        driver = get_driver()
        wait = WebDriverWait(driver, 25)

        # ── 1. Navigate to login page ─────────────────────────────────────────
        driver.get("https://m.facebook.com/login/")
        time.sleep(5)

        # Safety: if Chrome still ended up elsewhere, force-navigate
        if "facebook.com" not in driver.current_url:
            driver.execute_script(
                "window.location.replace('https://m.facebook.com/login/');"
            )
            time.sleep(6)

        # ── 2. Dismiss any cookie / consent banners ───────────────────────────
        driver.execute_script("""
            document.querySelectorAll('button, div[role="button"], a')
                .forEach(function(b) {
                    var t = (b.innerText || '').toLowerCase();
                    if (t.includes('accept') || t.includes('allow') ||
                        t.includes('only allow')) {
                        b.click();
                    }
                });
        """)
        time.sleep(2)

        # ── 3. Wait for the login form ────────────────────────────────────────
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Login form not found. See screenshot."

        # ── 4. Inject credentials via React-compatible synthetic events ────────
        # Plain `element.value = x` is silently ignored by React because it
        # bypasses the internal fiber state.  We must use the native setter and
        # then dispatch input/change events so React registers the new value.
        driver.execute_script("""
            var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;

            var emailEl = document.getElementsByName('email')[0];
            var passEl  = document.getElementsByName('pass')[0];

            setter.call(emailEl, arguments[0]);
            emailEl.dispatchEvent(new Event('input',  { bubbles: true }));
            emailEl.dispatchEvent(new Event('change', { bubbles: true }));

            setter.call(passEl, arguments[1]);
            passEl.dispatchEvent(new Event('input',  { bubbles: true }));
            passEl.dispatchEvent(new Event('change', { bubbles: true }));
        """, phone, password)

        time.sleep(1)

        # ── 5. Click the submit / login button ────────────────────────────────
        clicked = driver.execute_script("""
            var btn = document.querySelector('[data-sigil="m_login_button"]')
                   || document.querySelector('button[type="submit"]')
                   || document.getElementsByName('login')[0];
            if (btn) { btn.click(); return true; }
            return false;
        """)

        if not clicked:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Could not find login button. See screenshot."

        # ── 6. Poll until the session cookie appears ──────────────────────────
        time.sleep(4)   # give Facebook time to begin the redirect

        for _ in range(15):
            time.sleep(2)
            current_url  = driver.current_url
            cookie_dict  = {c['name']: c['value'] for c in driver.get_cookies()}

            if 'c_user' in cookie_dict:
                logger.info("Login successful for %s", phone)
                return driver.get_cookies(), None

            if "checkpoint" in current_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Checkpoint / 2FA detected. Manual action required."

            # Redirected away from login page but c_user not yet present —
            # wait a bit more and recheck once before continuing the loop.
            if "login" not in current_url and "facebook.com" in current_url:
                time.sleep(3)
                cookie_dict = {c['name']: c['value'] for c in driver.get_cookies()}
                if 'c_user' in cookie_dict:
                    logger.info("Login successful for %s (delayed)", phone)
                    return driver.get_cookies(), None

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Timed out. Wrong password or account is blocked."

    except Exception as exc:
        try:
            if driver:
                driver.save_screenshot(DEBUG_PHOTO)
        except Exception:
            pass
        return None, f"Runtime Error: {exc}"

    finally:
        # Always quit the driver to free resources
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── EXCEL STORAGE ─────────────────────────────────────────────────────────────

def save_cookies_to_excel(phone: str, cookies: list) -> None:
    c_json = json.dumps(cookies)

    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(["#", "Phone", "Cookies"])   # header row

    row_id = ws.max_row   # header = row 1 → first entry gets id 1, etc.
    ws.append([row_id, phone, c_json])
    wb.save(EXCEL_FILE)


# ── TELEGRAM HANDLERS ─────────────────────────────────────────────────────────

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
    with open(PASSWORD_FILE, "w", encoding="utf-8") as fh:
        fh.write(pw)
    await update.message.reply_text("✅ Password saved.")
    return ConversationHandler.END


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /add — asks for phone number."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📱 Send the phone number (e.g. +51928065251):")
    return WAITING_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives phone number, runs login in a thread, reports result."""
    phone = update.message.text.strip()

    if not os.path.exists(PASSWORD_FILE):
        await update.message.reply_text("❌ No password set. Use /setpw first.")
        return ConversationHandler.END

    with open(PASSWORD_FILE, "r", encoding="utf-8") as fh:
        pw = fh.read().strip()

    if not pw:
        await update.message.reply_text("❌ Password file is empty. Use /setpw again.")
        return ConversationHandler.END

    msg = await update.message.reply_text(f"⏳ Logging in as {phone} …")

    # Run the blocking Selenium work in a background thread so the bot stays responsive
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        if os.path.exists(DEBUG_PHOTO):
            with open(DEBUG_PHOTO, "rb") as photo_fh:
                await update.message.reply_photo(
                    photo=photo_fh,
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
        with open(EXCEL_FILE, "rb") as fh:
            await update.message.reply_document(
                document=fh,
                filename=EXCEL_FILE,
                caption="📊 Cookies Excel"
            )
    else:
        await update.message.reply_text("❌ No Excel file found yet.")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Simple one-shot commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dl", dl))

    # /setpw conversation: ask → receive password → done
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", setpw_entry)],
        states={
            WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password)],
        },
        fallbacks=[],
    ))

    # /add conversation: ask → receive phone → run login → done
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_entry)],
        states={
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
        },
        fallbacks=[],
    ))

    logger.info("Bot is running…")
    app.run_polling()


if __name__ == "__main__":
    main()
