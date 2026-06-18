from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import subprocess

URL = "https://w.pia.jp/t/top4dome2days-re/"
TARGET_BUTTONS = ["リセールチケット購入へ", "チケット詳細を見る", "購入に進む"]

# Chromeプロセスを強制終了
subprocess.run(
    ["powershell", "-Command", "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue"],
    check=False
)
time.sleep(1)

options = Options()
options.add_argument("--user-data-dir=C:/Temp/selenium_pia_profile")
options.add_argument("--no-first-run")
options.add_argument("--no-default-browser-check")
options.add_argument("--disable-extensions")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(options=options)
driver.get("https://t.pia.jp/")

input("Piaにログインしてから、Enterを押してください: ")
print("監視開始")


def find_target_button(driver):
    for btn_text in TARGET_BUTTONS:
        elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{btn_text}')]")
        clickable = [e for e in elements if e.is_displayed() and e.is_enabled()]
        if clickable:
            return btn_text, clickable[0]
    return None, None


# URLをリロードして最初のボタンが出るまで待機
while True:
    try:
        driver.get(URL)
        time.sleep(3)
        btn_text, element = find_target_button(driver)
        if element:
            break
        print("出品なし → 5秒後に再試行")
        time.sleep(5)
    except Exception as e:
        print(f"エラー: {e}")
        time.sleep(5)

# ボタンが見つかったら順次クリック
while True:
    try:
        print(f"「{btn_text}」をクリックします")
        element.click()

        if btn_text == "購入に進む":
            print("完了！あとは手動で操作してください")
            break

        time.sleep(3)
        btn_text, element = find_target_button(driver)
        if not element:
            print("次のボタンが見つかりませんでした")
            break
    except Exception as e:
        print(f"クリック中にエラー: {e}")
        break

print("ブラウザを開いたまま待機中")

while True:
    time.sleep(60)
