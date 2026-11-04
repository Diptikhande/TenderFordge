"""
Capture screenshots of Company Profile with proper Streamlit React event dispatching.
"""
import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import database

email = "view_test_99@test.com"
password = "SecretPass123!"

chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--window-size=1600,1200")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=chrome_options)
artifact_dir = r"C:\Users\Gouri\.gemini\antigravity\brain\62111859-0a1d-4e7b-ad44-f41e0467cc7e"
os.makedirs(artifact_dir, exist_ok=True)

try:
    print("Navigating to Streamlit app...")
    driver.get("http://127.0.0.1:8501")
    time.sleep(3)

    email_inputs = driver.find_elements(By.XPATH, "//input[@aria-label='Work Email']")
    pass_inputs = driver.find_elements(By.XPATH, "//input[@aria-label='Password']")

    if email_inputs and pass_inputs:
        print("Entering credentials...")
        email_inp = email_inputs[0]
        pass_inp = pass_inputs[0]

        # Use JS to set value and dispatch events
        driver.execute_script("""
            var emailEl = arguments[0];
            var passEl = arguments[1];
            var emailVal = arguments[2];
            var passVal = arguments[3];

            emailEl.value = emailVal;
            emailEl.dispatchEvent(new Event('input', { bubbles: true }));
            emailEl.dispatchEvent(new Event('change', { bubbles: true }));

            passEl.value = passVal;
            passEl.dispatchEvent(new Event('input', { bubbles: true }));
            passEl.dispatchEvent(new Event('change', { bubbles: true }));
        """, email_inp, pass_inp, email, password)
        time.sleep(1)

        # Press Enter on password input or click submit button
        pass_inp.send_keys(Keys.RETURN)
        time.sleep(4)

        # If still on login page, click submit
        if driver.find_elements(By.XPATH, "//div[@data-testid='stForm']//button"):
            btn = driver.find_element(By.XPATH, "//div[@data-testid='stForm']//button")
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)

    print("Checking current page elements after login...")
    radio_labels = driver.find_elements(By.XPATH, "//div[@data-testid='stRadio']//label")
    print("Sidebar options found:", [r.text for r in radio_labels])

    for r in radio_labels:
        if "Company Profile" in r.text:
            print("Found Company Profile, clicking...")
            driver.execute_script("arguments[0].click();", r)
            break

    time.sleep(4)

    # Check for tabs on Company Profile page
    tabs = driver.find_elements(By.XPATH, "//button[@role='tab']")
    print("Company Profile tabs found:", [t.text for t in tabs])

    # Save screenshot of Section 1 (General Information)
    driver.save_screenshot(os.path.join(artifact_dir, "company_profile_section1.png"))
    print("Saved company_profile_section1.png")

    # Click on Section 2 tab
    for t in tabs:
        if "Section 2" in t.text or "Tender-Specific" in t.text:
            print("Clicking Section 2 tab:", t.text)
            driver.execute_script("arguments[0].click();", t)
            break

    time.sleep(4)
    driver.save_screenshot(os.path.join(artifact_dir, "company_profile_section2.png"))
    print("Saved company_profile_section2.png")

finally:
    driver.quit()
    print("Done.")
