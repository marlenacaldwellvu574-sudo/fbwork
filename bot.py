import os
import logging
import asyncio
import json
import shutil
import tempfile
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
DEBUG_PHOTO   = "debug.png"
PROXY         = os.getenv("PROXY")  # optional: "socks5://user:pass@host:port"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_PW, WAITING_PHONE = 1, 2

# ── FACEBOOK LOGIN ────────────────────────────────────────────────────────────

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
    "Mobile/15E148 Safari/604.1"
)

def do_facebook_login(phone: str, password: str):
    """
    Login via requests session.
    Set PROXY env var to a residential proxy if Facebook blocks the server IP.
    e.g. PROXY=socks5://user:pass@proxy-host:1080
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    if PROXY:
        session.proxies = {"http": PROXY, "https": PROXY}
        logger.info("Using proxy: %s", PROXY)

    try:
        # ── 1. GET login page ─────────────────────────────────────────────────
        r1 = session.get("https://m.facebook.com/login/", timeout=30)
        r1.raise_for_status()

        # Extract CSRF tokens
        lsd     = re.search(r'name="lsd"\s+value="([^"]+)"', r1.text)
        jazoest = re.search(r'name="jazoest"\s+value="([^"]+)"', r1.text)
        lsd_val     = lsd.group(1)     if lsd     else ""
        jazoest_val = jazoest.group(1) if jazoest else ""
        logger.info("Tokens — lsd=%s jazoest=%s", lsd_val, jazoest_val)

        time.sleep(2)

        # ── 2. POST credentials ───────────────────────────────────────────────
        r2 = session.post(
            "https://m.facebook.com/login/device-based/regular/login/"
            "?refsrc=deprecated&lwv=100",
            data={
                "email":   phone,
                "pass":    password,
                "login":   "Log in",
                "lsd":     lsd_val,
                "jazoest": jazoest_val,
            },
            headers={"Referer": "https://m.facebook.com/login/"},
            allow_redirects=True,
            timeout=30,
        )

        logger.info("Login POST → %s [%d]", r2.url, r2.status_code)
        cookies = session.cookies.get_dict()
        logger.info("Cookies: %s", list(cookies.keys()))

        # ── 3. Success check ──────────────────────────────────────────────────
        if "c_user" in cookies:
            logger.info("✅ Login OK for %s", phone)
            return [{"name": k, "value": v} for k, v in session.cookies.items()], None

        # ── 4. Failure diagnosis ──────────────────────────────────────────────
        url  = r2.url.lower()
        html = r2.text.lower()

        if "checkpoint" in url or "checkpoint" in html:
            return None, (
                "⚠️ Facebook requires verification (checkpoint/2FA).\n"
                "Log into this account manually once, then try again."
            )
        if "wrong password" in html or "incorrect password" in html:
            return None, "❌ Wrong password."
        if "account has been disabled" in html or "account is disabled" in html:
            return None, "❌ Account disabled by Facebook."
        if "too many" in html or "try again later" in html or "unusual login" in html:
            return None, "❌ Facebook is rate-limiting this IP. Try later or add a proxy."

        # ── 5. IP block diagnosis (page looks like login page again) ──────────
        if "/login" in url or "log in" in html[:2000]:
            if not PROXY:
                return None, (
                    "❌ Facebook blocked the server IP.\n\n"
                    "Fix: Set the PROXY environment variable to a residential proxy.\n"
                    "Example: PROXY=socks5://user:pass@host:1080\n\n"
                    "Free option: use a SOCKS5 proxy from a residential provider."
                )
            return None, "❌ Proxy also blocked by Facebook. Try a different proxy."

        logger.warning("Unknown failure, HTML snippet: %s", r2.text[:300])
        return None, "❌ Login failed for unknown reason. Check logs."

    except requests.RequestException as e:
        return None, f"❌ Network error: {e}"
    except Exception as e:
        return None, f"❌ Runtime error: {e}"


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
    ws.append([ws.max_row, phone, c_json])
    wb.save(EXCEL_FILE)


# ── TELEGRAM HANDLERS ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "✅ Bot Ready.\n\n"
        "/setpw — Set Facebook password\n"
        "/add    — Add account (enter phone)\n"
        "/dl     — Download cookies Excel\n\n"
        "💡 If login fails due to IP block, set PROXY env var."
    )

async def setpw_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("Send the Facebook password:")
    return WAITING_PW

async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w", encoding="utf-8") as fh:
        fh.write(update.message.text.strip())
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
        await msg.edit_text(error)
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! {len(cookies)} cookies saved for {phone}.")
    return ConversationHandler.END

async def dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if os.path.exists(EXCEL_FILE):
        with open(EXCEL_FILE, "rb") as fh:
            await update.message.reply_document(document=fh, filename=EXCEL_FILE, caption="📊 Cookies Excel")
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
