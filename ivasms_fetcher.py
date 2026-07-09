import time
import hashlib
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
                (By.XPATH, "//input[@type='email' or @placeholder='Email address' or contains(@name,'email')]")
            ))
            email_field.clear()
            email_field.send_keys(self.email)
            
            password_field = self.driver.find_element(By.XPATH, "//input[@type='password' or contains(@placeholder,'Password')]")
            password_field.clear()
            password_field.send_keys(self.password)
            
            login_btn = self.driver.find_element(By.XPATH, "//button[@type='submit' or contains(text(),'Login') or contains(text(),'Sign')]")
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
        if "my_sms" in self.driver.current_url.lower():
            print("[IVAS] SMS Page Loaded!")
            return True
        return False
    
    def fetch_sms_list(self):
        try:
            rows = self.driver.find_elements(By.XPATH, "//tbody[@id='LiveTestSMS']/tr")
            sms_list = []
            
            for row in rows:
                try:
                    phone_elem = row.find_element(By.XPATH, ".//p[contains(@class,'CopyText')]")
                    phone = phone_elem.text.strip()
                    
                    sid_elem = row.find_element(By.XPATH, ".//td[contains(@class,'pe-card')]//div[contains(@class,'fw-semi-bold')]")
                    sid = sid_elem.text.strip()
                    
                    country_elem = row.find_element(By.XPATH, ".//h6/a")
                    country = country_elem.text.strip()
                    
                    msg_elem = row.find_element(By.XPATH, ".//td[contains(@class,'text-end')][last()]")
                    msg_text = msg_elem.text.strip()
                    
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
        import re
        patterns = [
            r'(?:code|otp|pin|verification|code is)[:\s]*(\d{4,8})',
            r'(\d{4,8})\s*(?:is your|your|code|otp|pin)',
            r'\b(\d{4,8})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def process_sms(self, sms_list):
        assigned = self.db.get_assigned_numbers()
        if not assigned:
            return
        
        assigned_phones = {phone: user_id for phone, user_id in assigned}
        
        for sms in sms_list:
            phone = sms['phone'].replace('+', '').replace(' ', '')
            msg = sms['message']
            
            # Check if this SMS is already processed
            sms_hash = hashlib.md5(f"{phone}{msg}".encode()).hexdigest()
            if self.db.is_sms_seen(sms_hash):
                continue
            
            self.db.mark_sms_seen(sms_hash)
            
            # Try to match with assigned numbers
            matched_user = None
            matched_phone = None
            
            for assigned_phone, user_id in assigned_phones.items():
                clean_assigned = assigned_phone.replace('+', '').replace(' ', '')
                if len(clean_assigned) >= 8:
                    if clean_assigned[-8:] in phone or phone[-8:] in clean_assigned:
                        matched_user = user_id
                        matched_phone = assigned_phone
                        break
            
            if matched_user and matched_phone:
                otp = self.extract_otp(msg)
                if otp:
                    self.db.mark_used_with_otp(matched_phone, otp)
                    
                    try:
                        self.bot.send_message(
                            chat_id=matched_user,
                            text=f"""
📩 <b>OTP Received!</b>

📞 <b>Number:</b> <code>{matched_phone}</code>
🔑 <b>OTP:</b> <code>{otp}</code>
📦 <b>Service:</b> {sms['service']}

📝 <i>Full SMS: {msg[:100]}</i>
""",
                            parse_mode='HTML'
                        )
                        print(f"[OTP] Sent to {matched_user}: {matched_phone} -> {otp}")
                    except Exception as e:
                        print(f"[OTP Error] {e}")
    
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
                        print("[IVAS] No data, refreshing page...")
                        self.driver.refresh()
                        time.sleep(5)
                        retry_count = 0
                
                interval = int(self.db.get_setting('fetch_interval') or 10)
                time.sleep(interval)
                
            except Exception as e:
                print(f"[IVAS] Loop Error: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    print("[IVAS] Max retries. Attempting re-login...")
                    try:
                        self.login()
                        self.goto_sms_page()
                    except:
                        pass
                    retry_count = 0
                time.sleep(5)
    
    def start(self):
        if self.running:
            return
        
        try:
            self.driver = self.setup_driver()
            if not self.login():
                print("[IVAS] Login failed!")
                self.driver.quit()
                return False
            
            if not self.goto_sms_page():
                print("[IVAS] Cannot access SMS page!")
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