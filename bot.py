import os
import logging
import asyncio
import json
import time
import re
import requests
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from openpyxl import Workbook, load_workbook

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN")
_admin_id  = os.getenv("ADMIN_ID")
ADMIN_ID   = int(_admin_id) if _admin_id else 0

PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE    = "cookies.xlsx"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_PW, WAITING_PHONE = 1, 2

# ── FACEBOOK LOGIN (requests-based, no Selenium) ──────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def do_facebook_login(phone: str, password: str):
    """
    Login to Facebook using requests session (no browser/Selenium).
    Returns: (cookie_list, None) on success
             (None, error_str)   on failure
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # ── 1. Load the login page to get tokens ─────────────────────────────
        logger.info("Fetching login page...")
        resp = session.get("https://m.facebook.com/login/", timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Extract lsd token (required by Facebook's login form)
        lsd = re.search(r'name="lsd"\s+value="([^"]+)"', html)
        jazoest = re.search(r'name="jazoest"\s+value="([^"]+)"', html)

        if not lsd:
            # Try alternate pattern
            lsd = re.search(r'"LSD",\[\],{"token":"([^"]+)"', html)

        lsd_val     = lsd.group(1) if lsd else ""
        jazoest_val = jazoest.group(1) if jazoest else ""

        logger.info("lsd=%s jazoest=%s", lsd_val, jazoest_val)

        # ── 2. POST login credentials ─────────────────────────────────────────
        time.sleep(2)  # brief human-like delay

        login_data = {
            "email":        phone,
            "pass":         password,
            "login":        "Log in",
            "lsd":          lsd_val,
            "jazoest":      jazoest_val,
        }

        logger.info("Posting login form...")
        resp2 = session.post(
            "https://m.facebook.com/login/device-based/regular/login/?refsrc=deprecated",
            data=login_data,
            headers={**HEADERS, "Referer": "https://m.facebook.com/login/"},
            allow_redirects=True,
            timeout=30,
        )

        logger.info("Post response URL: %s", resp2.url)
        logger.info("Status: %d", resp2.status_code)

        cookies = session.cookies.get_dict()
        logger.info("Cookies after login: %s", list(cookies.keys()))

        # ── 3. Check for successful login ─────────────────────────────────────
        if "c_user" in cookies:
            logger.info("Login successful for %s", phone)
            cookie_list = [
                {"name": k, "value": v} for k, v in session.cookies.items()
            ]
            return cookie_list, None

        # ── 4. Detect common failure reasons ──────────────────────────────────
        html2 = resp2.text.lower()

        if "checkpoint" in resp2.url or "checkpoint" in html2:
            return None, "Checkpoint / 2FA required. Log in manually first."

        if "wrong password" in html2 or "incorrect password" in html2:
            return None, "Wrong password."

        if "your account has been disabled" in html2:
            return None, "Account disabled by Facebook."

        if "too many" in html2 or "try again later" in html2:
            return None, "Rate limited by Facebook. Try again later."

        # Dump partial HTML for debugging if none of the above matched
        logger.warning("Login failed, partial HTML: %s", resp2.text[:500])
        return None, "Login failed. Facebook may be blocking this IP/account."

    except requests.RequestException as e:
        return None, f"Network error: {e}"
    except Exception as e:
        return None, f"Runtime error: {e}"


# ── EXCEL STORAGE ─────────────────────────────────────────────────────────────

def save_cookies_to_excel(phone: str, cookies: list) -> None:
    c_json = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(["#", "Phone", "Cookies"])
    row_id = ws.max_row
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
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📱 Send the phone number (e.g. +51928065251):")
    return WAITING_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
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
                document=fh, filename=EXCEL_FILE, caption="📊 Cookies Excel"
            )
    else:
        await update.message.reply_text("❌ No Excel file found yet.")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dl", dl))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", setpw_entry)],
        states={WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password)]},
        fallbacks=[],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_entry)],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)]},
        fallbacks=[],
    ))

    logger.info("Bot is running…")
    app.run_polling()


if __name__ == "__main__":
    main()
