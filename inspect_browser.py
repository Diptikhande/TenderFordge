import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

options = Options()
options.add_argument('--headless=new')
options.add_argument('--window-size=1440,900')
driver = webdriver.Chrome(options=options)
try:
    driver.get('http://localhost:8501')
    time.sleep(5)

    email_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="text"], input[type="email"]')
    password_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
    if email_inputs and password_inputs:
        email_inputs[0].clear()
        email_inputs[0].send_keys('persist_test@tenderforge-audit.internal')
        password_inputs[0].clear()
        password_inputs[0].send_keys('Persist#Test2026!Secret')
        time.sleep(1)
        submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[kind="primaryFormSubmit"]')
        submit_btn.click()
        time.sleep(6)
        print('Logged in, current URL:', driver.current_url)
        driver.save_screenshot('c:/TenderFordge/screenshot_dashboard.png')

        # Check sidebar
        sidebar = driver.find_elements(By.CSS_SELECTOR, '[data-testid="stSidebar"]')
        if sidebar:
            s = sidebar[0]
            print('Sidebar found! Computed styles:')
            print('opacity:', driver.execute_script('return window.getComputedStyle(arguments[0]).opacity;', s))
            print('filter:', driver.execute_script('return window.getComputedStyle(arguments[0]).filter;', s))
            print('bg:', driver.execute_script('return window.getComputedStyle(arguments[0]).backgroundColor;', s))
            print('color:', driver.execute_script('return window.getComputedStyle(arguments[0]).color;', s))
        else:
            print('Sidebar NOT found')

        # Check main
        main = driver.find_elements(By.CSS_SELECTOR, '[data-testid="stMain"]')
        if main:
            m = main[0]
            print('Main found! Computed styles:')
            print('opacity:', driver.execute_script('return window.getComputedStyle(arguments[0]).opacity;', m))
            print('filter:', driver.execute_script('return window.getComputedStyle(arguments[0]).filter;', m))
            print('bg:', driver.execute_script('return window.getComputedStyle(arguments[0]).backgroundColor;', m))

        # Check dimmed/filtered elements
        dimmed = driver.execute_script('''
            var dimmed = [];
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var cs = window.getComputedStyle(all[i]);
                if (cs.opacity !== "1" && all[i].offsetParent !== null) {
                    dimmed.push({tag: all[i].tagName, cls: all[i].className, opacity: cs.opacity});
                }
                if (cs.filter !== "none" && all[i].offsetParent !== null) {
                    dimmed.push({tag: all[i].tagName, cls: all[i].className, filter: cs.filter});
                }
            }
            return dimmed.slice(0, 20);
        ''')
        print('Dimmed/filtered visible elements count:', len(dimmed), dimmed[:5])

        # Navigate to Company Profile
        nav_labels = driver.find_elements(By.CSS_SELECTOR, '[data-testid="stSidebar"] [role="radiogroup"] label')
        print(f'Found {len(nav_labels)} nav labels')
        for nl in nav_labels:
            print('Nav label:', nl.text)
            if 'company profile' in nl.text.lower():
                nl.click()
                time.sleep(5)
                driver.save_screenshot('c:/TenderFordge/screenshot_company_profile.png')
                print('Navigated to Company Profile')
                break

finally:
    driver.quit()
