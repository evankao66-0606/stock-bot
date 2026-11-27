import os
from datetime import datetime
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ==========================================
# 1. 設定區（優先讀 Colab Secrets，沒有則讀下方變數）
# ==========================================
def get_secret(name: str, fallback: str = "") -> str:
    try:
        from google.colab import userdata
        val = userdata.get(name)
        if val:
            return val.strip()
    except Exception:
        pass
    return os.environ.get(name, fallback).strip()

TELEGRAM_TOKEN = get_secret("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID", "")

# 持倉清單（5 日線防守監控）
MY_PORTFOLIO = [
    "4919.TW",
    "3042.TW",
    "00685L.TW",
    "3008.TW",
    "3037.TW",
    "2327.TW",
    "8039.TW",
    "3163.TWO",
    "2308.TW",
    "2454.TW",
]

# 權證雷達候選標的（可依需求自行增減）
WARRANT_CANDIDATES = [
    "2330.TW",
    "2454.TW",
    "2308.TW",
    "2317.TW",
    "3037.TW",
    "2327.TW",
    "3008.TW",
    "4919.TW",
    "3042.TW",
    "8039.TW",
    "3163.TWO",
]

# 是否在 Colab 試跑時真的送出 Telegram 推播
SEND_TELEGRAM = True


# ==========================================
# 2. 資料抓取與指標計算
# ==========================================
def fetch_history(ticker: str, period: str = "2mo") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close", "Volume"]).copy()
    return df


def calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    if loss.empty or pd.isna(loss.iloc[-1]):
        return np.nan
    if loss.iloc[-1] == 0:
        return 100.0 if gain.iloc[-1] > 0 else 50.0
    rs = gain.iloc[-1] / loss.iloc[-1]
    return float(100.0 - (100.0 / (1.0 + rs)))


def check_portfolio_ma5(portfolio: list[str]) -> tuple[list[str], int, int]:
    """
    回傳：(表格列字串清單, 跌破檔數, 資料異常檔數)
    """
    rows = []
    broken_count = 0
    missing_count = 0

    for ticker in portfolio:
        code = ticker.split(".")[0]
        df = fetch_history(ticker, period="1mo")

        if df.empty or len(df) < 5:
            missing_count += 1
            rows.append(f"{code:<6} {'NaN':>6} {'NaN':>6} ⚠️ 資料不足")
            continue

        close_val = float(df["Close"].iloc[-1])
        ma5_val = float(df["Close"].rolling(window=5).mean().iloc[-1])

        if np.isnan(close_val) or np.isnan(ma5_val):
            missing_count += 1
            rows.append(f"{code:<6} {'NaN':>6} {'NaN':>6} ⚠️ 資料不足")
            continue

        if close_val < ma5_val:
            broken_count += 1
            status = "⚠️ 跌破MA5"
        else:
            status = "✅ 穩守MA5"

        rows.append(f"{code:<6} {close_val:>6.1f} {ma5_val:>6.1f} {status}")

    return rows, broken_count, missing_count


def scan_warrant_radar(candidates: list[str]) -> list[str]:
    """
    範例爆量突破條件：
    1. 當日收盤價高於近 10 日（不含當日）最高收盤價
    2. 當日成交量 >= 近 10 日（不含當日）均量 * 1.8
    3. RSI(14) 介於 50 與 85 之間
    """
    hits = []
    for ticker in candidates:
        code = ticker.split(".")[0]
        df = fetch_history(ticker, period="2mo")
        if df.empty or len(df) < 16:
            continue

        close_series = df["Close"]
        vol_series = df["Volume"]

        last_close = float(close_series.iloc[-1])
        prev_10_high = float(close_series.iloc[-11:-1].max())

        last_vol = float(vol_series.iloc[-1])
        prev_10_vol_avg = float(vol_series.iloc[-11:-1].mean())

        rsi_val = calc_rsi(close_series, period=14)

        if (
            not np.isnan(last_close)
            and not np.isnan(prev_10_high)
            and prev_10_vol_avg > 0
            and not np.isnan(rsi_val)
        ):
            vol_ratio = last_vol / prev_10_vol_avg
            if last_close > prev_10_high and vol_ratio >= 1.8 and 50 <= rsi_val <= 85:
                hits.append(f"• {code}：收盤 {last_close:.1f}｜量比 {vol_ratio:.1f}x｜RSI {rsi_val:.1f}")

    return hits


# ==========================================
# 3. 組合報表與推播
# ==========================================
def build_report() -> str:
    today_str = datetime.now().strftime("%Y-%m-%d")
    hits = scan_warrant_radar(WARRANT_CANDIDATES)
    rows, broken_count, missing_count = check_portfolio_ma5(MY_PORTFOLIO)

    warrant_section = (
        "\n".join(hits)
        if hits
        else "今日無符合爆量突破條件之權證標的."
    )

    table_header = "代碼     收盤    MA5      狀態"
    table_body = "\n".join(rows)

    if broken_count > 0:
        footer = f"⚠️ 注意：共有 {broken_count} 檔持倉跌破 5 日線，請留意權證出場時機！"
    elif missing_count > 0:
        footer = f"⚠️ 提醒：有 {missing_count} 檔標的資料不足（NaN），請確認代號或盤後資料。"
    else:
        footer = "🎯 所有持倉皆安全站穩 5 日線之上."

    report = (
        f"⚡ 台股權證標的雷達報告 ({today_str})\n\n"
        f"📊【今日強勢權證標的】\n"
        f"{warrant_section}\n\n"
        f"🛡️【庫藏股 5 日線防守監控】(持倉 {len(MY_PORTFOLIO)} 檔)\n"
        f"```\n{table_header}\n{table_body}\n```\n\n"
        f"{footer}"
    )
    return report


def send_telegram(text: str) -> None:
    if not SEND_TELEGRAM:
        print("（目前 SEND_TELEGRAM = False，僅在終端機預覽）")
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 尚未設定 TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID，略過推播。")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code != 200:
        # 若 Markdown 格式造成 400 錯誤，退回純文字重送
        fallback_payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        resp2 = requests.post(url, json=fallback_payload, timeout=15)
        print(f"首次 Markdown 推播狀態 {resp.status_code}，純文字重送狀態：{resp2.status_code}")
    else:
        print("✅ Telegram 推播成功")


if __name__ == "__main__":
    report_text = build_report()
    print(report_text)
    send_telegram(report_text)
