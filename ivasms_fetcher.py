import time
import hashlib
import re
import threading
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class IVASMSFetcher:
    def __init__(self, email, password, database, bot_app):
        self.email = email
        self.password = password
        self.db = database
        self.bot = bot_app.bot
        self.app = bot_app
        self.driver = None
        self.running = False
        self.thread = None
    
    def setup_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    
    def login(self):
        print("[IVAS] Logging in...")
        self.driver.get("https://www.ivasms.com/login")
        wait = WebDriverWait(self.driver, 20)
        try:
            email_field = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//input[@type='email' or @placeholder='Email address' or contains(@name,'email')]")))
            email_field.clear()
            email_field.send_keys(self.email)
            
            password_field = self.driver.find_element(By.XPATH, "//input[@type='password' or contains(@placeholder,'Password')]")
            password_field.clear()
            password_field.send_keys(self.password)
            
            login_btn = self.driver.find_element(By.XPATH, "//button[@type='submit' or contains(text(),'Login')]")
            login_btn.click()
            time.sleep(5)
            
            if "login" not in self.driver.current_url.lower():
                print("[IVAS] Login SUCCESS!")
                return True
            else:
                print("[IVAS] Login FAILED!")
                return False
        except Exception as e:
            print(f"[IVAS] Login Error: {e}")
            return False
    
    def goto_sms_page(self):
        print("[IVAS] Navigating to SMS page...")
        self.driver.get("https://www.ivasms.com/portal/live/my_sms")
        time.sleep(5)
        return "my_sms" in self.driver.current_url.lower()
    
    def fetch_sms_list(self):
        try:
            rows = self.driver.find_elements(By.XPATH, "//tbody[@id='LiveTestSMS']/tr")
            sms_list = []
            for row in rows:
                try:
                    phone = row.find_element(By.XPATH, ".//p[contains(@class,'CopyText')]").text.strip()
                    sid = row.find_element(By.XPATH, ".//td[contains(@class,'pe-card')]//div[contains(@class,'fw-semi-bold')]").text.strip()
                    country = row.find_element(By.XPATH, ".//h6/a").text.strip()
                    msg_text = row.find_element(By.XPATH, ".//td[contains(@class,'text-end')][last()]").text.strip()
                    
                    sms_list.append({
                        'phone': phone,
                        'service': sid,
                        'country': country,
                        'message': msg_text
                    })
                except:
                    continue
            return sms_list
        except Exception as e:
            print(f"[IVAS] Fetch Error: {e}")
            return []
    
    def extract_otp(self, message):
        patterns = [
            r'(?:code|otp|pin|verification|code is)[:\s]*(\d{4,8})',
            r'(\d{4,8})\s*(?:is your|your|code|otp|pin)',
            r'\b(\d{4,6})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def send_to_group(self, phone, country, service, message, otp):
        """OTP group e SMS forward kore"""
        group_id = self.db.get_otp_group_id()
        if not group_id:
            print("[IVAS] No OTP group set!")
            return
        
        try:
            msg = f"""
📱 <b>New SMS</b>
🌍 {country} | 📦 {service}
📞 <code>{phone}</code>
📩 <code>{message}</code>
🔑 OTP: <code>{otp}</code>
"""
            self.bot.send_message(chat_id=group_id, text=msg, parse_mode='HTML')
            print(f"[GROUP] Sent: {phone} -> {otp}")
        except Exception as e:
            print(f"[GROUP] Error: {e}")
    
    def process_sms(self, sms_list):
        """SMS process kore group e pathay"""
        for sms in sms_list:
            phone = sms['phone'].replace('+', '').replace(' ', '')
            msg = sms['message']
            
            sms_hash = hashlib.md5(f"{phone}{msg}".encode()).hexdigest()
            if self.db.is_sms_seen(sms_hash):
                continue
            
            self.db.mark_sms_seen(sms_hash)
            otp = self.extract_otp(msg)
            
            if otp:
                print(f"[NEW SMS] {phone} | {sms['service']} | OTP: {otp}")
                # Group e send koro
                self.send_to_group(phone, sms['country'], sms['service'], msg, otp)
            else:
                print(f"[SMS] {phone} | No OTP found in message")
    
    def run_loop(self):
        print("[IVAS] Starting fetch loop...")
        retry_count = 0
        max_retries = 3
        
        while self.running:
            try:
                sms_list = self.fetch_sms_list()
                if sms_list:
                    self.process_sms(sms_list)
                    retry_count = 0
                else:
                    retry_count += 1
                    if retry_count >= max_retries:
                        self.driver.refresh()
                        time.sleep(5)
                        retry_count = 0
                
                interval = int(self.db.get_setting('fetch_interval') or 10)
                time.sleep(interval)
                
            except Exception as e:
                print(f"[IVAS] Loop Error: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    try:
                        self.login()
                        self.goto_sms_page()
                    except:
                        pass
                    retry_count = 0
                time.sleep(5)
    
    def start(self):
        if self.running:
            return True
        try:
            self.driver = self.setup_driver()
            if not self.login():
                self.driver.quit()
                return False
            if not self.goto_sms_page():
                self.driver.quit()
                return False
            
            self.running = True
            self.thread = threading.Thread(target=self.run_loop, daemon=True)
            self.thread.start()
            print("[IVAS] SMS Fetcher Started!")
            return True
        except Exception as e:
            print(f"[IVAS] Start Error: {e}")
            return False
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=10)
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        print("[IVAS] SMS Fetcher Stopped.")