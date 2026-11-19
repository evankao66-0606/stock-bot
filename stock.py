from datetime import datetime
import os
import pandas as pd
import requests
import yfinance as yf
os.environ["TELEGRAM_BOT_TOKEN"] = "9156071043:AAEYKJJIA_BCc053CiKEm9wD3USTx3YiAUI"
os.environ["TELEGRAM_CHAT_ID"] = "8302884631"

# ---------------------------------------------------------
# 權證專屬參數設定
# ----------------------------
N_HIGH = 20  # 創 20 日新高
VOL_MULT = 2.0  # 權證爆發力，量能倍數 2.0 倍
RSI_LOW = 55
RSI_HIGH = 80
MA_PERIOD = 20
MIN_VOL = 2000  # 5日均量 > 2000張

# 從環境變數讀取 Telegram 憑證
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# 1. 候選掃描清單
candidate_stocks = [
    # 半導體 / IC設計
    "2330.TW", "2303.TW", "2454.TW", "3711.TW", "2379.TW",
    "3034.TW", "2408.TW", "3443.TW", "3661.TW",
    # AI 伺服器 / 電子代工
    "2382.TW", "3231.TW", "2356.TW", "6669.TW", "2317.TW",
    "2308.TW", "3017.TW", "4938.TW", "2357.TW", "2376.TW",
    # 散熱 / PCB / 光電 / 零組件
    "3324.TW", "3037.TW", "2368.TW", "3008.TW", "3406.TW",
    "2327.TW", "2345.TW",
    # 航運 / 重電 / 金融 / 傳產權值
    "2603.TW", "2609.TW", "2615.TW", "2618.TW",
    "1519.TW", "1513.TW", "1504.TW", "1605.TW", "2002.TW",
    "2881.TW", "2882.TW", "2891.TW",
    # 大股東增持 / 中小型題材精選 (10檔)
    "3693.TW",  # 營邦
    "6664.TWO",  # 群翊
    "3715.TW",  # 定穎投控
    "1815.TWO",  # 富喬
    "1802.TW",  # 台玻
    "2465.TW",  # 麗臺
    "4916.TW",  # 事欣科
    "8103.TWO",  # 瀚荃
    "7848.TW",  # 錸億鑫
    "3441.TWO",  # 聯一光 (注意處置)
]

# 2. 已買入權證之現股監控清單（已排除 3357.TW）
my_portfolio = ["4919.TW", "3042.TW", "00685L.TW"]

def get_warrant_eligible_stocks():
    """從證交所與櫃買中心開放資料取得目前可發行權證的上市櫃股票代碼集合"""
    eligible = set()

    # 1. 上市可發行權證標的（.TW）
    try:
        url_twse = "https://openapi.twse.com.tw/v1/opendata/t187ap46_L_1"
        res_twse = requests.get(url_twse, timeout=10)
        if res_twse.status_code == 200:
            data_twse = res_twse.json()
            for item in data_twse:
                code = str(item.get("標的代號", "")).strip()
                if code:
                    eligible.add(f"{code}.TW")
            print(f"✅ 取得上市權證標的 {len(eligible)} 檔")
    except Exception as e:
        print(f"⚠️ 取得上市權證清單失敗: {e}")

    # 2. 上櫃可發行權證標的（.TWO）
    tpex_count = 0
    tpex_urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_warrant_target",
        "https://www.tpex.org.tw/web/stock/warrant/warrant_target/r_warrant_target_result.php?l=zh-tw&o=json",
    ]
    for url_tpex in tpex_urls:
        try:
            res_tpex = requests.get(url_tpex, timeout=10)
            if res_tpex.status_code == 200:
                payload = res_tpex.json()
                rows = payload if isinstance(payload, list) else payload.get("aaData", [])
                for row in rows:
                    if isinstance(row, dict):
                        code = str(
                            row.get("UnderlyingStockCode")
                            or row.get("標的代號")
                            or row.get("StockCode")
                            or ""
                        ).strip()
                    elif isinstance(row, (list, tuple)) and len(row) > 0:
                        code = str(row[0]).strip()
                    else:
                        code = ""
                    if code:
                        eligible.add(f"{code}.TWO")
                        tpex_count += 1
                if tpex_count > 0:
                    print(f"✅ 取得上櫃權證標的 {tpex_count} 檔")
                    break
        except Exception as e:
            print(f"⚠️ 嘗試上櫃權證清單端點失敗 ({url_tpex}): {e}")

    if len(eligible) > 0:
        print(f"✅ 合計可發行權證標的共 {len(eligible)} 檔")
        return eligible

    print("⚠️ 無法取得上市櫃權證清單，改以候選清單全數掃描")
    return set(candidate_stocks)



def send_telegram_message(message):
    """發送 Telegram 推播通知"""
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("⚠️ 尚未設定 Telegram 環境變數，跳過推播。")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ Telegram 訊息推播成功！")
        else:
            print(f"❌ 推播失敗: {response.text}")
    except Exception as e:
        print(f"❌ 推播發生錯誤: {e}")


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def run_warrant_radar():
    today_str = datetime.now().strftime("%Y-%m-%d")
    msg_report = f"⚡ *台股權證標的雷達報告* ({today_str})\n\n"

    # 取得官方權證標的並過濾候選清單
    eligible_set = get_warrant_eligible_stocks()
    if eligible_set:
        scan_list = [s for s in candidate_stocks if s in eligible_set]
    else:
        scan_list = candidate_stocks

    print(f"實際掃描有發行權證的個股數：{len(scan_list)} 檔")

    # ==========================================
    # 第一部分：掃描具權證操作價值的新突破股
    # ==========================================
    new_picks = []
    for ticker in scan_list:
        try:
            df = yf.download(ticker, period="3mo", progress=False)
            if df.empty or len(df) < MA_PERIOD + N_HIGH:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                close = df["Close"].iloc[:, 0]
                volume = df["Volume"].iloc[:, 0] / 1000
            else:
                close = df["Close"]
                volume = df["Volume"] / 1000

            ma20 = close.rolling(MA_PERIOD).mean()
            high_n = close.rolling(N_HIGH).max()
            vol_ma5 = volume.rolling(5).mean()
            rsi = calculate_rsi(close, 14)

            latest_close = close.iloc[-1]
            prev_close = close.iloc[-2]

            cond_new_high = latest_close >= high_n.iloc[-2]
            cond_volume = volume.iloc[-1] >= vol_ma5.iloc[-1] * VOL_MULT
            cond_ma = latest_close > ma20.iloc[-1]
            cond_rsi = (rsi.iloc[-1] >= RSI_LOW) & (rsi.iloc[-1] <= RSI_HIGH)
            cond_liquidity = vol_ma5.iloc[-1] >= MIN_VOL

            if (
                cond_new_high
                and cond_volume
                and cond_ma
                and cond_rsi
                and cond_liquidity
            ):
                vol_ratio = volume.iloc[-1] / vol_ma5.iloc[-1]
                change = (latest_close / prev_close - 1) * 100
                new_picks.append({
                    "代碼": ticker.replace(".TW", ""),
                    "收盤": round(latest_close, 1),
                    "漲幅%": round(change, 1),
                    "量倍": round(vol_ratio, 1),
                })
        except Exception:
            continue

    if len(new_picks) > 0:
        df_picks = pd.DataFrame(new_picks).sort_values("量倍", ascending=False)
        msg_report += f"🔥 *【今日強勢權證標的】* ({len(df_picks)}檔)\n```\n"
        msg_report += df_picks.to_string(index=False)
        msg_report += "\n```\n\n"
    else:
        msg_report += "📊 *【今日強勢權證標的】*\n今日無符合爆量突破條件之權證標的。\n\n"

    # ==========================================
    # 第二部分：已買入權證之現股監控 (MA5)
    # ==========================================
    if len(my_portfolio) > 0:
        portfolio_status = []
        alerts_count = 0
        for ticker in my_portfolio:
            try:
                df = yf.download(ticker, period="1mo", progress=False)
                if df.empty or len(df) < 10:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    close = df["Close"].iloc[:, 0]
                else:
                    close = df["Close"]

                ma5 = close.rolling(5).mean()
                latest_close = close.iloc[-1]
                latest_ma5 = ma5.iloc[-1]

                if latest_close < latest_ma5:
                    status = "⚠️ 跌破MA5"
                    alerts_count += 1
                else:
                    status = "✅ 穩守MA5"

                portfolio_status.append({
                    "代碼": ticker.replace(".TW", ""),
                    "收盤": round(latest_close, 1),
                    "MA5": round(latest_ma5, 1),
                    "狀態": status,
                })
            except Exception as e:
                print(f"檢查持倉 {ticker} 失敗: {e}")

        if len(portfolio_status) > 0:
            df_port = pd.DataFrame(portfolio_status)
            msg_report += f"🛡️ *【庫藏股 5 日線防守監控】* (持倉 {len(df_port)} 檔)\n```\n"
            msg_report += df_port.to_string(index=False)
            msg_report += "\n```\n"
            if alerts_count > 0:
                msg_report += f"\n⚠️ *注意：共有 {alerts_count} 檔持倉跌破 5 日線，請留意權證出場時機！*"
            else:
                msg_report += "\n🎯 *所有持倉皆安全站穩 5 日線之上。*"

    send_telegram_message(msg_report)


if __name__ == "__main__":
    run_warrant_radar()
