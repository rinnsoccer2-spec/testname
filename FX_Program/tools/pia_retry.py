from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException
import time
import subprocess
import winsound

URL = "https://tran.pia.jp/top4dome2days-re/"
TARGET_BUTTONS = ["リセールチケット購入へ", "ログイン", "チケット詳細を見る"]

ticket_info = ""


def kill_chrome():
    subprocess.run(
        ["powershell", "-Command", "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue"],
        check=False
    )
    time.sleep(1)


def create_driver():
    options = Options()
    options.add_argument("--user-data-dir=C:/Temp/selenium_pia_profile")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=options)


def notify(ticket_info):
    print("\n========== チケット取得成功！ ==========")
    print(ticket_info if ticket_info else "詳細情報を取得できませんでした")
    print("=========================================\n")
    for _ in range(5):
        winsound.Beep(1000, 500)
        time.sleep(0.2)


def find_target_button(driver):
    global ticket_info

    # リセールチケット購入へ は1枚のみフィルタリング
    buttons = driver.find_elements(By.XPATH, "//*[contains(text(), 'リセールチケット購入へ')]")
    for btn in buttons:
        if not (btn.is_displayed() and btn.is_enabled()):
            continue
        try:
            parent = btn
            for _ in range(8):
                parent = parent.find_element(By.XPATH, "..")
                text = parent.text
                if "1枚" in text:
                    ticket_info = text
                    return "リセールチケット購入へ", btn
        except Exception:
            pass

    # ログイン・チケット詳細を見る はフィルタリング不要
    for btn_text in ["ログイン", "チケット詳細を見る"]:
        elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{btn_text}')]")
        clickable = [e for e in elements if e.is_displayed() and e.is_enabled()]
        if clickable:
            return btn_text, clickable[0]

    return None, None


def navigate_to_event(driver):
    """URLを開いて7/5の詳細をクリックする"""
    driver.get(URL)
    time.sleep(3)

    # 7/5を含む祖先要素内の詳細ボタンを直接取得
    try:
        btn = driver.find_element(By.XPATH,
            "//*[contains(text(), '詳細')"
            " and (ancestor::*[contains(., '7/5')] or ancestor::*[contains(., '7月5日')])]"
        )
        btn.click()
        time.sleep(2)
        print("7/5の詳細をクリックしました")
        return
    except Exception:
        pass

    # フォールバック: 詳細ボタンが2つある場合、2番目（7/5）をクリック
    try:
        detail_btns = [b for b in driver.find_elements(By.XPATH, "//*[contains(text(), '詳細')]")
                       if b.is_displayed() and b.is_enabled()]
        if len(detail_btns) >= 2:
            detail_btns[1].click()
            time.sleep(2)
            print("7/5の詳細をクリックしました（フォールバック）")
            return
    except Exception:
        pass

    print("7/5の詳細ボタンが見つかりませんでした")


# 初回起動
kill_chrome()
driver = create_driver()
driver.get("https://t.pia.jp/")
input("Piaにログインしてから、Enterを押してください: ")

navigate_to_event(driver)
print("監視開始")

while True:
    try:
        # ボタンが出るまでURLをリロードし続ける
        while True:
            navigate_to_event(driver)
            btn_text, element = find_target_button(driver)
            if element:
                break
            print("出品なし → 5秒後に再試行")
            time.sleep(5)

        # ボタンを順次クリック
        while True:
            print(f"「{btn_text}」をクリックします")
            element.click()
            time.sleep(3)

            btn_text, element = find_target_button(driver)
            if not element:
                # 「公演情報」「料金詳細を確認する」タブを開いて情報取得
                for tab_text in ["公演情報", "料金詳細を確認する"]:
                    try:
                        tab = driver.find_element(By.XPATH, f"//*[contains(text(), '{tab_text}')]")
                        tab.click()
                        time.sleep(1)
                    except Exception:
                        pass
                try:
                    ticket_info = driver.find_element(By.TAG_NAME, "body").text
                except Exception:
                    pass
                notify(ticket_info)
                print("あとは手動で操作してください")
                break

        break  # 完了

    except InvalidSessionIdException:
        print("Chromeがクラッシュしました。再起動します...")
        kill_chrome()
        time.sleep(2)
        driver = create_driver()
        navigate_to_event(driver)
        print("再起動完了。監視を再開します")

    except Exception as e:
        print(f"エラー: {e}")
        time.sleep(5)

print("ブラウザを開いたまま待機中")

while True:
    time.sleep(60)
