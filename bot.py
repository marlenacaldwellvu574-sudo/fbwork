import os
import logging
import asyncio
import json
import tempfile
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

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PASSWORD_FILE = "fb_password.txt"
EXCEL_FILE = "cookies.xlsx"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
WAITING_PW, WAITING_PHONE = 1, 2

def get_driver():
    profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    
    # High-quality Mobile User Agent
    options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    wait = WebDriverWait(driver, 20)
    try:
        # Use the specific 'touch' login endpoint which is more bot-stable
        driver.get("https://m.facebook.com/login/?refsrc=deprecated&_rdr")
        time.sleep(5)

        # 1. FORCE DISMISS COOKIES (The common cause of 'Not Interactable')
        driver.execute_script("""
            var buttons = document.querySelectorAll('button, div[role="button"]');
            for(var i=0; i<buttons.length; i++) {
                var txt = buttons[i].innerText.toLowerCase();
                if(txt.includes('accept') || txt.includes('allow') || txt.includes('only allow')) {
                    buttons[i].click();
                }
            }
        """)
        time.sleep(2)

        # 2. FIND AND INJECT CREDENTIALS VIA JAVASCRIPT
        # This bypasses the 'Interactable' check entirely
        try:
            wait.until(EC.presence_of_element_located((By.NAME, "email")))
            driver.execute_script(f"document.getElementsByName('email')[0].value='{phone}';")
            driver.execute_script(f"document.getElementsByName('pass')[0].value='{password}';")
            logging.info("Credentials injected via JS.")
        except Exception:
            return None, "Login fields did not load in time."

        # 3. FORCE LOGIN CLICK
        driver.execute_script("""
            var loginBtn = document.getElementsByName('login')[0] || 
                           document.querySelector('button[type="submit"]') ||
                           document.querySelector('button[name="login"]');
            loginBtn.click();
        """)
        
        logging.info("Login clicked. Monitoring cookies...")
        
        # 4. MONITOR FOR SUCCESS (Up to 15 seconds)
        for _ in range(15):
            time.sleep(1)
            current_cookies = {c['name']: c['value'] for c in driver.get_cookies()}
            if 'c_user' in current_cookies:
                logging.info("Success! Cookies captured.")
                return driver.get_cookies(), None
            
            # Check for common blocks
            curr_url = driver.current_url
            if "checkpoint" in curr_url:
                return None, "Blocked: 2FA or Checkpoint active on this account."
            if "login/device-based/edit-user" in curr_url:
                # FB is asking to 'Save Password', click 'Not Now' or just grab cookies anyway
                return driver.get_cookies(), None

        driver.save_screenshot("final_fail.png")
        return None, "Login timed out. Check final_fail.png"

    except Exception as e:
        return None, f"Runtime Error: {str(e)}"
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

# --- BOT INTERFACE ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🚀 FB Cookie Bot\n/setpw - Save Password\n/add - Run Login\n/dl - Get Excel")

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
        await update.message.reply_text("❌ Use /setpw first.")
        return ConversationHandler.END
    await update.message.reply_text("Send Phone/Email:")
    return WAITING_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    with open(PASSWORD_FILE, "r") as f: pw = f.read().strip()
    msg = await update.message.reply_text(f"⏳ Logging into {phone}...")
    
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
    else:
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! Cookies for {phone} added to Excel.")
    return ConversationHandler.END

def save_cookies_to_excel(phone, cookies):
    c_json = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook(); ws = wb.active
        ws.append(["#", "Account", "Cookie Data"])
    
    ws.append([ws.max_row, phone, c_json])
    wb.save(EXCEL_FILE)

async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"), filename="fb_cookies.xlsx")
    else:
        await update.message.reply_text("No data found.")

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
