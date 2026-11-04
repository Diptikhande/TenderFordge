import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

options = Options()
options.add_argument('--headless=new')
options.add_argument('--window-size=1440,900')
driver = webdriver.Chrome(options=options)
try:
    driver.get('http://localhost:8501')
    time.sleep(4)
    driver.find_elements(By.CSS_SELECTOR, 'input[type="text"], input[type="email"]')[0].send_keys('persist_test@tenderforge-audit.internal')
    driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')[0].send_keys('Persist#Test2026!Secret')
    driver.find_element(By.CSS_SELECTOR, 'button[kind="primaryFormSubmit"]').click()
    time.sleep(5)

    info = driver.execute_script('''
        var s = document.querySelector('[data-testid="stSidebar"]');
        if (!s) return 'no sidebar';
        var res = [];
        var all = s.querySelectorAll('*');
        res.push({tag: s.tagName, cls: s.className, bg: window.getComputedStyle(s).backgroundColor, testid: s.getAttribute('data-testid')});
        for (var i = 0; i < Math.min(all.length, 15); i++) {
            var el = all[i];
            var cs = window.getComputedStyle(el);
            res.push({tag: el.tagName, cls: el.className, bg: cs.backgroundColor, testid: el.getAttribute('data-testid')});
        }
        return res;
    ''')
    for item in info:
        print(item)
finally:
    driver.quit()
