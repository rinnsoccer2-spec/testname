from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
import subprocess

URL = "https://t.pia.jp/pia/ticketInformation.do?eventCd=2604917&rlsCd=002"
# Chromeプロセスを強制終了
subprocess.run(
    ["powershell", "-Command", "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue"],
    check=False
)
time.sleep(1)

options = Options()
options.add_argument("--no-first-run")
options.add_argument("--no-default-browser-check")
options.add_argument("--disable-extensions")

driver = webdriver.Chrome(options=options)
driver.get("https://t.pia.jp/")

input("Piaにログインしてから、Enterを押してください: ")
print("監視開始")

while True:
    try:
        driver.get(URL)

        time.sleep(3)

        page = driver.page_source

        SALE_TEXT = "※本サイトでの発売開始日時となります。予定枚数終了しだい発売終了となります。"
        if SALE_TEXT not in page:
            print("発売情報未表示 → 5秒後に再試行")
            time.sleep(5)
            continue

        print("目的のページに到達しました！（発売情報を確認）")
        break

    except Exception as e:
        print(f"エラー: {e}")
        time.sleep(5)

print("ブラウザを開いたまま待機中")

while True:
    time.sleep(60)
