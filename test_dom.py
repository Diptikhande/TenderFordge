import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument('--headless=new')
options.add_argument('--window-size=1440,900')
driver = webdriver.Chrome(options=options)
try:
    driver.get('http://localhost:8501')
    time.sleep(5)
    res = driver.execute_script("""
        var allStyles = Array.from(document.querySelectorAll('style')).map(s => s.innerHTML.length);
        var inMarkdown = Array.from(document.querySelectorAll('[data-testid="stMarkdownContainer"] style')).map(s => s.innerHTML.length);
        var bodyStyle = window.getComputedStyle(document.body);
        return {
            allStyles: allStyles,
            inMarkdown: inMarkdown,
            fontFamily: bodyStyle.fontFamily
        };
    """)
    print('Styles info:', res)
finally:
    driver.quit()
