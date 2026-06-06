from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

URL = "https://t.pia.jp/pia/event/event.do?eventCd=2604917"

options = Options()

driver = webdriver.Chrome(options=options)

print("監視開始")

while True:
    try:
        driver.get(URL)

        # 読み込み待ち
        time.sleep(3)

        page = driver.page_source

        waiting_words = [
            "アクセスが集中",
            "しばらくお待ちください",
            "混雑",
            "Queue",
            "待合室"
        ]

        if any(word in page for word in waiting_words):
            print("混雑中 → 5秒後に再試行")
            time.sleep(5)
            continue

        print("目的のページに到達しました！")
        break

    except Exception as e:
        print(f"エラー: {e}")
        time.sleep(5)

# 成功後はブラウザを開いたまま維持
print("ブラウザを開いたまま待機中")

while True:
    time.sleep(60)