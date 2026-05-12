import os
import logging
import asyncio
import json
import tempfile
import shutil
import time
import glob
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

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN not set")
if not _admin_id:
    raise SystemExit("ERROR: ADMIN_ID not set")

ADMIN_ID = int(_admin_id)
PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE = "cookies.xlsx"
DEBUG_PHOTO = "final_fail.png"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
WAITING_PW, WAITING_PHONE = 1, 2

# --- HELPERS ---

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
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 20)
    try:
        driver.get("https://m.facebook.com/login/?refsrc=deprecated&_rdr")
        time.sleep(5)

        # Force dismiss overlays via JS
        driver.execute_script("""
            var buttons = document.querySelectorAll('button, div[role="button"]');
            for(var i=0; i<buttons.length; i++) {
                var txt = (buttons[i].innerText || '').toLowerCase();
                if(txt.includes('accept') || txt.includes('allow') || txt.includes('only allow')) {
                    buttons[i].click();
                    break;
                }
            }
        """)
        time.sleep(2)

        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Login fields did not load. See attached screenshot."

        # Inject credentials via JS to avoid "Element not interactable" errors
        safe_phone = phone.replace("'", "\\'")
        safe_password = password.replace("'", "\\'")
        driver.execute_script(f"document.getElementsByName('email')[0].value='{safe_phone}';")
        driver.execute_script(f"document.getElementsByName('pass')[0].value='{safe_password}';")

        # Click login
        driver.execute_script("""
            var btn = document.getElementsByName('login')[0] || document.querySelector('button[type="submit"]');
            if(btn) btn.click();
        """)

        # Poll for cookies
        for i in range(20):
            time.sleep(1)
            current_cookies = {c['name']: c['value'] for c in driver.get_cookies()}
            curr_url = driver.current_url

            if 'c_user' in current_cookies:
                return driver.get_cookies(), None
            if "checkpoint" in curr_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Blocked: 2FA/Checkpoint detected."
            if "wrong" in driver.page_source.lower():
                return None, "Incorrect password."

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Login timed out."

    except Exception as e:
        driver.save_screenshot(DEBUG_PHOTO)
        return None, f"Runtime Error: {str(e)}"
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

# --- TELEGRAM HANDLERS ---

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    with open(PASSWORD_FILE, "r") as f:
        pw = f.read().strip()
    
    msg = await update.message.reply_text(f"⏳ Logging into {phone}...")

    # Cleanup old debug photos before start
    if os.path.exists(DEBUG_PHOTO): os.remove(DEBUG_PHOTO)

    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        # THE LAZY WAY: Send screenshot if it exists
        if os.path.exists(DEBUG_PHOTO):
            await update.message.reply_photo(photo=open(DEBUG_PHOTO, 'rb'), caption="This is what the bot saw during the failure.")
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! {len(cookies)} cookies saved.")
    
    return ConversationHandler.END

# --- REMAINING HANDLERS (Same as your provided version) ---

def save_cookies_to_excel(phone, cookies):
    c_json = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE); ws = wb.active
    else:
        wb = Workbook(); ws = wb.active
        ws.append(["#", "Account", "Cookie Data"])
    ws.append([ws.max_row, phone, c_json])
    wb.save(EXCEL_FILE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("FB Bot Active\n/setpw - Set Pass\n/add - Run Login\n/dl - Get Excel")

async def cmd_setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send FB password:")
    return WAITING_PW

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w") as f: f.write(update.message.text.strip())
    await update.message.reply_text("Password saved.")
    return ConversationHandler.END

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not os.path.exists(PASSWORD_FILE):
        await update.message.reply_text("Set password first.")
        return ConversationHandler.END
    await update.message.reply_text("Send phone number:")
    return WAITING_PHONE

async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"), filename="fb_cookies.xlsx")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dl", cmd_dl))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", cmd_setpw)],
        states={WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)]},
        fallbacks=[]
    ))
    app.run_polling()

if __name__ == "__main__":
    main()
