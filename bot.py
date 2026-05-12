import os
import logging
import asyncio
import json
import tempfile
import shutil
import glob
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

# --- CORE LOGIC ---

def get_driver():
    profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # STEALTH SETTINGS
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Use a real-world User Agent
    options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")

    driver = webdriver.Chrome(options=options)
    
    # Patch the 'webdriver' flag so FB thinks it's a real person
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver, profile_dir

def do_facebook_login(phone, password):
    driver, profile_dir = get_driver()
    try:
        # 1. Use the mobile touch interface (m.facebook.com) - easier to automate
        driver.get("https://m.facebook.com/login/")
        time.sleep(4)

        # 2. Try to find the email/phone field
        # FB mobile uses different IDs than desktop
        email_selectors = ["m_login_email", "email"]
        email_field = None
        
        for selector in email_selectors:
            try:
                email_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, selector))
                )
                break
            except: continue

        if not email_field:
            driver.save_screenshot("error_view.png")
            return None, "Login form not found. Screenshot saved as error_view.png"

        # 3. Enter credentials slowly to mimic human
        email_field.send_keys(phone)
        time.sleep(1)
        
        pass_field = driver.find_element(By.NAME, "pass")
        pass_field.send_keys(password)
        time.sleep(1)

        # 4. Submit
        try:
            login_btn = driver.find_element(By.NAME, "login")
        except:
            login_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        
        login_btn.click()
        
        # 5. Wait for redirect
        logging.info("Waiting for session...")
        time.sleep(10) 

        current_url = driver.current_url
        cookies = driver.get_cookies()
        cookie_names = [c["name"] for c in cookies]

        # 6. Validation
        if "checkpoint" in current_url:
            return None, "Locked: Checkpoint/Approve on another device."
        
        if "c_user" in cookie_names or "xs" in cookie_names:
            return cookies, None
        else:
            driver.save_screenshot("login_failed.png")
            return None, "Login failed. Check logs and login_failed.png"

    except Exception as e:
        return None, f"System Error: {str(e)}"
    finally:
        driver.quit()
        shutil.rmtree(profile_dir, ignore_errors=True)

# --- TELEGRAM HANDLERS (Same logic, refined) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("FB Cookie Bot Active.\n/setpw - Set Pass\n/add - Get Cookies\n/dl - Get Excel")

async def cmd_setpw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send the FB password now:")
    return WAITING_PW

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open(PASSWORD_FILE, "w") as f: f.write(update.message.text.strip())
    await update.message.reply_text("Password saved.")
    return ConversationHandler.END

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send Phone Number (+CountryCode):")
    return WAITING_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    with open(PASSWORD_FILE, "r") as f: pw = f.read().strip()
    
    msg = await update.message.reply_text(f"Attempting login for {phone}...")
    
    # Run heavy selenium in a thread to keep bot responsive
    cookies, error = await asyncio.to_thread(do_facebook_login, phone, pw)

    if error:
        await msg.edit_text(f"❌ {error}")
    else:
        # Save to Excel
        save_cookies_to_excel(phone, cookies)
        await msg.edit_text(f"✅ Success! {len(cookies)} cookies saved.")
    
    return ConversationHandler.END

def save_cookies_to_excel(phone, cookies):
    c_str = json.dumps(cookies)
    if os.path.exists(EXCEL_FILE):
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(["Row", "Phone", "Cookies"])
    
    ws.append([ws.max_row, phone, c_str])
    wb.save(EXCEL_FILE)

async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(EXCEL_FILE):
        await update.message.reply_document(open(EXCEL_FILE, "rb"))
    else:
        await update.message.reply_text("No data yet.")

# --- MAIN ---

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

    logging.info("Bot is live.")
    app.run_polling()

if __name__ == "__main__":
    main()
