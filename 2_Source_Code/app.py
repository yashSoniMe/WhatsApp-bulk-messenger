import os
import time
import queue
import pandas as pd
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.utils import secure_filename

from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.common.action_chains import ActionChains

app = Flask(__name__)
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Log channel queue for frontend stream
log_queue = queue.Queue()

def log_to_ui(msg: str):
    log_queue.put(msg)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream-logs')
def stream_logs():
    def generate():
        while True:
            try:
                log_msg = log_queue.get(timeout=20)
                yield f"data: {log_msg}\n\n"
                if "[Campaign Completed]" in log_msg or "[Campaign Aborted]" in log_msg:
                    break
            except queue.Empty:
                yield "data: [Keep-Alive] Running...\n\n"
    return Response(generate(), mimetype='text/event-stream')

def run_whatsapp_campaign(excel_path, global_template):
    driver = None
    try:
        log_to_ui("Reading Excel list...")
        df = pd.read_excel(excel_path)
        df.columns = [str(c).strip().lower() for c in df.columns]

        # Verify both required columns are present
        if 'phone' not in df.columns:
            log_to_ui("❌ Error: Missing 'phone' column in Excel file.")
            log_to_ui("[Campaign Aborted]")
            return
        if 'name' not in df.columns:
            log_to_ui("❌ Error: Missing 'name' column in Excel file.")
            log_to_ui("[Campaign Aborted]")
            return

        log_to_ui("Starting Microsoft Edge via Selenium...")
        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1200,900")
        options.add_argument(f"user-data-dir={os.path.join(UPLOAD_FOLDER, 'edge_profile')}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.use_chromium = True

        driver = webdriver.Edge(service=Service(EdgeChromiumDriverManager().install()), options=options)
        
        log_to_ui("Opening WhatsApp Web. Verify with QR code if needed...")
        driver.get("https://web.whatsapp.com/")
        
        # Initial login guard wait
        wait = WebDriverWait(driver, 90)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='chat-list']")))
        log_to_ui("✅ Authenticated successfully! Initializing personalized broadcast sequence...")
        time.sleep(1)

        for index, row in df.iterrows():
            phone = str(row['phone']).strip().replace('+', '').split('.')[0]
            raw_name = str(row['name']).strip()
            
            # Use 'Customer' as a fallback if name is empty in the sheet
            name = raw_name if raw_name != 'nan' and raw_name else "Customer"
            
            if not phone or phone == "nan": 
                continue

            # Dynamically replace the placeholder with this recipient's name
            personalized_message = global_template.replace("{name}", name)

            log_to_ui(f"Opening conversation for row {index + 1}: +91{phone} ({name})...")
            driver.get(f"https://web.whatsapp.com/send?phone=91{phone}")
            
            try:
                chat_wait = WebDriverWait(driver, 20)
                
                # Check for bad contact numbers instantly
                try:
                    invalid = driver.find_elements(By.XPATH, "//*[contains(text(), 'Phone number shared via url is invalid')]")
                    if invalid:
                        log_to_ui(f"❌ Error: +{phone} is not registered on WhatsApp. Skipping...")
                        continue
                except Exception:
                    pass

                # Locate the primary message textbox container explicitly
                message_box = chat_wait.until(
                    EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true']"))
                )
                time.sleep(2.5)  # Let interface completely settle
                
                log_to_ui(f"Typing personalized message...")
                lines = personalized_message.split("\n")
                for i, line in enumerate(lines):
                    message_box.send_keys(line)
                    if i < len(lines) - 1:
                        # Shift+Enter = newline inside WhatsApp's box, without sending
                        ActionChains(driver).key_down(webdriver.Keys.CONTROL).send_keys(webdriver.Keys.ENTER).key_up(webdriver.Keys.CONTROL).perform()
                time.sleep(1)

                log_to_ui(f"Sending message...")
                message_box.send_keys(webdriver.Keys.ENTER)
                time.sleep(1)
                
                log_to_ui(f"Sending message...")
                message_box.send_keys(webdriver.Keys.ENTER)
                log_to_ui(f"✅ Message sent successfully to {name} (+{phone})!")
                
                # Cooldown period
                time.sleep(2)

            except Exception as e:
                log_to_ui(f"❌ Delivery failed for +{phone}. Error: {str(e)[:45]}...")

        log_to_ui("🎉 Campaign completed successfully.")
        log_to_ui("[Campaign Completed]")

    except Exception as e:
        log_to_ui(f"❌ Global runtime break: {str(e)}")
        log_to_ui("[Campaign Aborted]")
    finally:
        if driver:
            time.sleep(1)
            driver.quit()

@app.route('/run', methods=['POST'])
def start():
    if 'excel_file' not in request.files:
        return jsonify({"error": "Excel file required"}), 400

    excel_file = request.files['excel_file']
    msg = request.form.get('message', '')

    excel_path = os.path.join(UPLOAD_FOLDER, 'temp.xlsx')
    excel_file.save(excel_path)

    import threading
    t = threading.Thread(target=run_whatsapp_campaign, args=(excel_path, msg))
    t.start()

    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(port=5000, debug=True)