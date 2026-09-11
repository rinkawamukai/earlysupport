import html
import os
from pathlib import Path
import sys
import tkinter
import time
import re
from datetime import datetime
from tkinter import messagebox
from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright
from common import err_log, STORAGE
from scrape_execute import execute_scrape_actions



def decide_save_path(cfg):
    """
    現在日付から出力Excelの保存先パスを決定する。
    保存先パスに既にファイルがある場合は上書き確認を行う。
    """

    try:
        out_folder = cfg["OUT_FOLDER"]
        now = datetime.now() - relativedelta(months=1)
        xlsx_name = cfg["XLSX_NAME"].format(
            yyyy=now.year,
            mm=now.month
        )
        save_path = os.path.join(out_folder, xlsx_name)

        if os.path.exists(save_path):
            root = tkinter.Tk()
            root.withdraw()
            if not messagebox.askyesno(
                "上書き確認",
                f"{save_path}\nは存在します。\n上書きしますか？"
            ):
                sys.exit(0)

        return save_path

    except Exception as e:
        err_log("エラー：EXCEL保存先パス取得失敗", e)
    return save_path



def create_or_update_storage(storage_path, base_url):    
    """
    SDPに手動でのログイン操作を促し、Playwrightのstorage_stateを保存または更新する。
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url, wait_until="load")
            page.wait_for_url("**/HomePage.do", timeout=180000)
            context.storage_state(path=storage_path)
        except Exception as e:
            err_log("エラー：手動ログイン失敗", e)
        finally:
            context.close()
            browser.close()



def safe_goto(context, url, retries=5, base_wait=2, timeout=60000):
    """
    安定したページ遷移を行う
    """

    for attempt in range(1, retries + 1):
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return page

        except Exception as e:
            # pageは閉じる
            page.close()

            # リトライ待機
            sleep_time = base_wait * attempt
            time.sleep(sleep_time)

    return None



def is_logged_in(page):    
    """
    「ダッシュボード」の表示有無から、SDPにログイン済みかどうかを判定する。
    """

    try:
        return page.get_by_role(
            "tab", name="ダッシュボード"
        ).first.is_visible()
    except Exception:
        return False



def start_browser(base_url):    
    """
    storage_stateを利用してブラウザを起動する。
    storage_stateが利用不可の場合は、
    create_or_update_storageによる再ログインをした上で起動する。
    """

    p = sync_playwright().start()
    browser = p.chromium.launch(
        channel="msedge",
        headless=False,
        args=["--start-maximized"]
    )

    try:
        context = browser.new_context(
            storage_state=STORAGE,
            accept_downloads=True
        )
        return p, browser, context

    except Exception:
        # storageがないもしくは無効
        browser.close()
        p.stop()

        create_or_update_storage(STORAGE, base_url)

        p = sync_playwright().start()
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--start-maximized"]
        )
        context = browser.new_context(
            storage_state=STORAGE,
            accept_downloads=True
        )
        return p, browser, context


def report_dl(save_path, cfg):    
    """
    SDPへアクセスして、設定に基づいてレポート（XLSX）をダウンロードする。
    """

    base_url = cfg["BASE_URL"]

    p, browser, context = start_browser(base_url)

    try:

        page = safe_goto(context, base_url)
        if not page:
            err_log("エラー：URL 接続失敗")


        # ログイン状態チェック
        if not is_logged_in(page):
            context.close()
            browser.close()
            p.stop()

            create_or_update_storage(STORAGE, base_url)

            p, browser, context = start_browser(base_url)
            page = safe_goto(context, base_url)

            if not page or not is_logged_in(page):
                err_log("エラー：SDP画面取得失敗")

        # スクレイピング処理
        execute_scrape_actions(page, cfg, save_path)

        # 最新storage保存
        context.storage_state(path=STORAGE)

    except Exception as e:
        err_log("エラー：XLSX ダウンロード処理失敗", e)
    return p, browser, context

    
def clean_text(text):
    """
    取得したSDP詳細テキストの可読性をあげるために、整形する
    """

    # HTMLエンティティをデコード
    for _ in range(5):
        text = html.unescape(text)

    # HTMLタグ削除
    text = re.sub(r"<.*?>", "", text)

    # URL削除
    text = re.sub(r"https?://\S+", "", text)

    # メールヘッダ削除
    text = re.sub(r"^(From|Sent|To|Cc|Subject):.*$", "", text, flags=re.MULTILINE)

    # --- 罫線・区切り削除
    #text = re.sub(r"[-＝=]{5,}.*", "", text)
    #text = re.sub(r"(・-){3,}.*", "", text)

    # --- 署名っぽい行削除
    #text = re.sub(r".*@.*", "", text)
    #text = re.sub(r"(内線|外線|TEL|電話).*", "", text)

    # 空白を除去
    text = text.replace("\xa0", " ")   # ノーブレークスペース
    text = text.replace("\u200b", "")  # ゼロ幅スペース

    # 空白正規化（全角含む）
    text = re.sub(r"[ \t\u3000]+", " ", text)

    # 各行の前後だけ削除（改行は保持）
    lines = text.splitlines()

    cleaned_lines = []
    for line in lines:
        stripped = line.strip(" 　\t")
        cleaned_lines.append(stripped)

    text = "\n".join(cleaned_lines)

    # 空行整理
    
    text = re.sub(
        r"\n{1,}",
        lambda m: "\n" * max(1, int(len(m.group(0)) // 2.5)),
        text
    )

    return text.strip()



def fetch_single_page(page, url, cfg):
    """
    SDP詳細ページを取得し、
    「スレッド単位」で整形済みテキストを返す
    """

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # スレッド行待機
        page.wait_for_function(
            "(selector) => document.querySelectorAll(selector).length > 0",
            arg=cfg["thread_rows"],
            timeout=30000
        )

        title = page.locator(cfg["title"]).inner_text().strip()
        
        rows = page.locator(cfg["thread_rows"])
        page.wait_for_timeout(1000)
        results = []

        for i in range(rows.count()):
            row = rows.nth(i)

            # スレッド展開
            convo = row.locator("div.conversation-info").first
            if not convo.is_visible():
                for selector in cfg["thread_openers"]:
                    btn = row.locator(selector)
                    if btn.count():
                        btn.first.click()
                        break

            page.wait_for_timeout(200)

            # 本文取得
            body = None
            text = row.locator(cfg["rtc_block"]).last.inner_text()
            
            if isinstance(text, list):
                text = "\n".join(text)
            results.append(clean_text(text))
        # タイトル付与
        if title:
            results.insert(0, f"<<title>>\n{title}")

        return "\n\n<<chat>>\n".join(results)

    except Exception as e:
        err_log(f"エラー：詳細ページ取得失敗 ({url})", e)
        return ""



def fetch_single_page_bk(page, url, cfg):    
    """
    指定URLの詳細ページを開く。
    タイトルとスレッド内容を展開・抽出して文字列として返す。
    """

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # スレッドがDOMに現れるまで待つ
        page.wait_for_function(
            """
            (selector) => document.querySelectorAll(selector).length > 0
            """,
            arg=cfg["thread_rows"],
            timeout=30000
        )

        title = page.locator(cfg["title"]).inner_text()

        rows = page.locator(cfg["thread_rows"])
        page.wait_for_timeout(1000)

        # 全スレッドを開いた状態にする
        for i in range(rows.count()):
            row = rows.nth(i)

            convo = row.locator("div.conversation-info").first
            # すでに表示されていれば何もしない
            if convo.is_visible():
                continue
            
            # 表示されてなければ開く
            for selector in cfg["thread_openers"]:
                target = row.locator(selector)
                if target.count():
                    target.first.click()
                    break

        page.wait_for_timeout(5000)

        # 全文取得
        rtc_blocks = page.locator(cfg["rtc_block"])
        rtc_blocks.wait_for(timeout=60000)

        texts = [
            # リスト内包表記
            # rtc_blockのすべてのテキストをループで取り出し、stripで整形してリスト化
            t.strip()
            for t in rtc_blocks.all_text_contents()
            if t and t.strip()
        ]
        
        if title:
            texts.insert(0, title)

        return "\n".join(texts) 

    except Exception as e:
        err_log(f"エラー：詳細ページ取得失敗 ({url})※プロンプト作成以外のプロセスは正常に完了", e)



def format_results(results):
    """
    取得したテキスト（LIST形式）をプロンプト作成用の文字列に整形する。
    """

    formatted = []
    for i, item in enumerate(results, start=1):
        formatted.append(f"【{i}件目】\n{item}\n\n")
    return "\n".join(formatted)



def scrape_details_text(context, url_list, cfg):    
    """
    URL一覧を巡回して詳細テキストを取得し、
    重複を除外したうえで整形済み文字列として返す。
    """

    try:
        unique_urls = list(dict.fromkeys(url_list))
        results = []

        page = context.new_page()

        for url in unique_urls:
            text = fetch_single_page(page, url, cfg)
            if text:
                results.append(text)

        results = format_results(results)
        file_path = Path("result.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(results))

        return results

    except Exception as e:
        err_log("エラー：URL 巡回スクレイピング失敗\n※プロンプト作成以外のプロセスは正常に完了", e)

    finally:
        page.close()

