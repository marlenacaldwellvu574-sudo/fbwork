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
DEBUG_PHOTO = "debug_state.png"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
WAITING_PW, WAITING_PHONE = 1, 2

# --- BROWSER ENGINE ---

def get_driver():
    profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    options = Options()
    
    # FORCE DIRECT URL ACCESS
    target_url = "https://m.facebook.com/login/"
    options.add_argument(f"--app={target_url}") 
    
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # iPhone User Agent
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    driver = webdriver.Chrome(options=options)
    
    # Patch navigator.webdriver
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 30)
    try:
        # Step 1: Force Navigation
        driver.get("https://m.facebook.com/login/")
        time.sleep(5)
        
        # Double check if we are on Google/Blank and force JS redirect
        if "facebook.com" not in driver.current_url:
            logging.info("Stuck on Google/Blank page. Forcing JS Redirect...")
            driver.execute_script("window.location.replace('https://m.facebook.com/login/');")
            time.sleep(7)

        # Step 2: Clear Cookie/Consent Banners
        driver.execute_script("""
            var selectors = ['button', 'div[role="button"]', 'a', 'span'];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    var t = (el.innerText || '').toLowerCase();
                    if(t.includes('accept') || t.includes('allow') || t.includes('only allow')) {
                        el.click();
                    }
                });
            });
        """)
        time.sleep(3)

        # Step 3: Inject Credentials
        try:
            # Re-locate fields in case of redirect
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
            
            safe_phone = phone.replace("'", "\\'")
            safe_pass = password.replace("'", "\\'")
            
            # Injecting values directly into the DOM
            driver.execute_script(f"document.getElementsByName('email')[0].value='{safe_phone}';")
            driver.execute_script(f"document.getElementsByName('pass')[0].value='{safe_pass}';")
            time.sleep(1)
            
            # Click Login
            driver.execute_script("""
                var btn = document.getElementsByName('login')[0] || 
                          document.querySelector('button[type="submit"]') ||
                          document.querySelector('button[name="login"]');
                if(btn) btn.click();
            """)
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "Facebook login form could not be found. See screenshot."

        # Step 4: Capture Session
        logging.info("Checking for session tokens...")
        for _ in range(20):
            time.sleep(2)
            cookies = {c['name']: c['value'] for c in driver.get_cookies()}
            
            if 'c_user' in cookies:
                logging.info("Login success!")
                return driver.get_cookies(), None
            
            if "checkpoint" in driver.current_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Checkpoint detected (2FA/Verification required)."
            
            # Successful redirect to profile/feed
            if "m.facebook.com/home.php" in driver.current_url or "save-device" in driver.current_url:
                return driver.get_cookies(), None

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Timed out waiting for login to process."

    except Exception as e:
        driver.save_screenshot(DEBUG_PHOTO)
        return None, f"Runtime Error: {str(e)}"
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

# --- BOT LOGIC ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🚀 Bot Ready\n/setpw - Set Pass\n/add - Get Cookies\n/dl - Get Excel")

async def cmd_setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send FB Password:")
    return WAITING_PW

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w") as f: f.write(update.message.text.strip())
    await update.message.reply_text("✅ Password Saved.")
    return ConversationHandler.END

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not os.path.exists(PASSWORD_FILE):
        await update.message.reply_text("Set password first.")
        return ConversationHandler.END
    await update.message.reply_text("Send Phone/Email:")
    return WAITING_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    with open(PASSWORD_FILE, "r") as f: pw = f.read().strip()
    msg = await update.message.reply_text(f"⏳ Logging in: {phone}...")
    
    if os.path.exists(DEBUG_PHOTO): os.remove(DEBUG_PHOTO)

    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        if os.path.exists(DEBUG_PHOTO):
            await update.message.reply_photo(photo=open(DEBUG_PHOTO, 'rb'), caption="Bot Error Snapshot")
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
        ws.append(["#", "Account", "Cookies"])
    ws.append([ws.max_row, phone, c_json])
    wb.save(EXCEL_FILE)

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
