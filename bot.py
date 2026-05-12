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
ADMIN_ID = int(_admin_id) if _admin_id else 0

PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE = "cookies.xlsx"
DEBUG_PHOTO = "debug.png"

logging.basicConfig(level=logging.INFO)
WAITING_PW, WAITING_PHONE = 1, 2

# --- BROWSER ENGINE ---

def get_driver():
    # Use a persistent path for stability
    profile_dir = os.path.abspath("chrome_profile")
    options = Options()
    
    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # iPhone User Agent is best for m.facebook.com
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )
    
    driver = webdriver.Chrome(options=options)
    
    # Patch the webdriver flag
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except: pass
    
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 25)
    try:
        # Step 1: Force Navigation and bypass the "Google Search" screen
        driver.get("https://m.facebook.com/login/")
        time.sleep(5)
        
        # If we are stuck on Google or a blank page, force it via JS
        if "facebook.com" not in driver.current_url:
            driver.execute_script("window.location.replace('https://m.facebook.com/login/');")
            time.sleep(6)

        # Step 2: Nuclear Cookie Dismissal
        driver.execute_script("""
            var btns = document.querySelectorAll('button, div[role="button"], a');
            btns.forEach(b => {
                var t = (b.innerText || '').toLowerCase();
                if(t.includes('accept') || t.includes('allow') || t.includes('only allow')) b.click();
            });
        """)
        time.sleep(2)

        # Step 3: JS-Only Injection (Bypasses "Not Interactable")
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
            
            s_phone = phone.replace("'", "\\'")
            s_pass = password.replace("'", "\\'")
            
            driver.execute_script(f"document.getElementsByName('email')[0].value='{s_phone}';")
            driver.execute_script(f"document.getElementsByName('pass')[0].value='{s_pass}';")
            
            # Force click the login button
            driver.execute_script("""
                var b = document.getElementsByName('login')[0] || document.querySelector('button[type="submit"]');
                if(b) b.click();
            """)
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Login form not found. See screenshot."

        # Step 4: Session Polling
        for _ in range(15):
            time.sleep(2)
            cookies = {c['name']: c['value'] for c in driver.get_cookies()}
            if 'c_user' in cookies:
                return driver.get_cookies(), None
            if "checkpoint" in driver.current_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Checkpoint detected (2FA)."

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Timed out waiting for session."

    except Exception as e:
        driver.save_screenshot(DEBUG_PHOTO)
        return None, f"Runtime Error: {str(e)}"
    finally:
        driver.quit()
        # Keep the profile_dir for next time, don't delete it!

# --- TELEGRAM HANDLERS ---

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    pw = open(PASSWORD_FILE).read().strip() if os.path.exists(PASSWORD_FILE) else None
    
    if not pw:
        await update.message.reply_text("No password set. Use /setpw first.")
        return ConversationHandler.END

    msg = await update.message.reply_text(f"⏳ Logging into {phone}...")
    
    # Run login logic in separate thread
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        if os.path.exists(DEBUG_PHOTO):
            await update.message.reply_photo(photo=open(DEBUG_PHOTO, 'rb'), caption="Bot Error View")
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! {len(cookies)} cookies saved.")
    
    return ConversationHandler.END

def save_cookies_to_excel(phone, cookies):
    c_json = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE); ws = wb.active
    else:
        wb = Workbook(); ws = wb.active
        ws.append(["#", "Phone", "Cookies"])
    ws.append([ws.max_row, phone, c_json])
    wb.save(EXCEL_FILE)

# (Standard /start, /setpw, and /dl handlers below)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Bot Ready.\n/setpw - Set Pass\n/add - Get Cookies\n/dl - Get Excel")

async def setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send password:")
    return WAITING_PW

async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w") as f: f.write(update.message.text.strip())
    await update.message.reply_text("Saved.")
    return ConversationHandler.END

async def dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dl", dl))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", setpw)],
        states={WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", CommandHandler("add", lambda u, c: update.message.reply_text("Send phone")))], # Minimal entry
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)]},
        fallbacks=[]
    ))
    # Corrected conversation entry
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", lambda u, c: u.message.reply_text("Send phone"))],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)]},
        fallbacks=[]
    ))
    app.run_polling()

if __name__ == "__main__":
    main()
