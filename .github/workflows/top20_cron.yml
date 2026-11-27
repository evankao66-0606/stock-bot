from datetime import datetime
import os
import re
import pandas as pd
import requests
import yfinance as yf


def get_env_config() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 環境變數")
    return token, chat_id


def send_telegram_message(message: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram 推播失敗 ({resp.status_code}): {resp.text}")
    print("✅ Telegram 訊息推播成功！")


def get_all_tw_stocks() -> dict[str, str]:
    """
    取得完整上市（.TW）與上櫃（.TWO）股票對照表：{ "2330.TW": "台積電", ... }
    """
    stock_map: dict[str, str] = {}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    def _valid_stock_code(code: str) -> bool:
        return bool(re.fullmatch(r"\d{4,6}", code))

    def _parse_rows(data, suffix: str) -> None:
        if not isinstance(data, list):
            return
        code_candidates = [
            "Code",
            "SecuritiesCompanyCode",
            "公司代號",
            "股票代號",
            "證券代號",
            "代號",
        ]
        name_candidates = [
            "Name",
            "CompanyName",
            "公司名稱",
            "股票名稱",
            "證券名稱",
            "名稱",
        ]
        for row in data:
            if not isinstance(row, dict):
                continue
            code = ""
            name = ""
            for k in code_candidates:
                if k in row and row[k] is not None:
                    val = str(row[k]).strip()
                    if _valid_stock_code(val):
                        code = val
                        break
            for k in name_candidates:
                if k in row and row[k] is not None:
                    val = str(row[k]).strip()
                    if val:
                        name = val
                        break
            if code and name:
                if any(w in name for w in ["購", "售", "牛", "熊", "展延"]):
                    continue
                stock_map[f"{code}{suffix}"] = name

    # 1. 上市股票清單（TWSE OpenAPI）
    twse_endpoints = [
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    ]
    for url in twse_endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                _parse_rows(r.json(), ".TW")
                if any(k.endswith(".TW") for k in stock_map):
                    break
        except Exception:
            continue

    # 2. 上櫃股票清單（TPEx OpenAPI）
    tpex_endpoints = [
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    ]
    for url in tpex_endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                _parse_rows(r.json(), ".TWO")
                if any(k.endswith(".TWO") for k in stock_map):
                    break
        except Exception:
            continue

    return stock_map


def scan_top20_strong_stocks(
    stock_map: dict[str, str], top_n: int = 20, batch_size: int = 120
) -> list[str]:
    """
    從完整股票清單中批次抓取日線，挑出最多 top_n 檔突破或強勢股（含代號與名稱）。
    """
    tickers = list(stock_map.keys())
    candidates: list[tuple[float, str]] = []

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            data = yf.download(
                batch,
                period="2mo",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
            if data is None or data.empty:
                continue

            for ticker in batch:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if ticker not in data.columns.get_level_values(0):
                            continue
                        df = data[ticker].dropna(subset=["Close", "Volume"])
                    else:
                        df = data.dropna(subset=["Close", "Volume"])

                    if len(df) < 22:
                        continue

                    close = df["Close"].astype(float)
                    vol = df["Volume"].astype(float)

                    last_close = float(close.iloc[-1])
                    prev_close = float(close.iloc[-2])
                    if prev_close <= 0:
                        continue

                    pct_change = (last_close - prev_close) / prev_close * 100
                    ma5 = float(close.rolling(5).mean().iloc[-1])
                    ma20 = float(close.rolling(20).mean().iloc[-1])
                    prev_10_high = float(close.iloc[-11:-1].max())

                    last_vol = float(vol.iloc[-1])
                    avg_vol_5 = float(vol.iloc[-6:-1].mean())
                    if avg_vol_5 < 1_000_000:  # 5日均量至少 1,000 張
                        continue

                    vol_ratio = last_vol / avg_vol_5

                    # 突破或強勢條件：站穩 MA5/MA20 且（創10日高放量 或 強勢放量上漲）
                    is_breakout = (
                        (last_close > prev_10_high)
                        and (last_close > ma5 > ma20)
                        and (vol_ratio >= 1.3)
                    )
                    is_strong = (
                        (last_close > ma5)
                        and (last_close > ma20)
                        and (pct_change >= 3.0)
                        and (vol_ratio >= 1.2)
                    )

                    if is_breakout or is_strong:
                        tag = "突破" if is_breakout else "強勢"
                        code = ticker.split(".")[0]
                        name = stock_map.get(ticker, "")
                        score = vol_ratio * 2.0 + max(pct_change, 0)
                        line = (
                            f"• [{tag}] {code} {name} "
                            f"（+{pct_change:.1f}%｜收{last_close:.1f}｜量{vol_ratio:.1f}x）"
                        )
                        candidates.append((score, line))
                except Exception:
                    continue
        except Exception:
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in candidates[:top_n]]


def main() -> None:
    token, chat_id = get_env_config()
    all_stocks = get_all_tw_stocks()
    print(f"✅ 成功取得上市櫃股票數：{len(all_stocks)} 檔")

    top20 = scan_top20_strong_stocks(all_stocks, top_n=20)
    today_str = datetime.now().strftime("%Y-%m-%d")

    if top20:
        report = (
            f"🚀 *【突破／強勢股前 {len(top20)} 檔】* ({today_str})\n"
            + "\n".join(top20)
        )
    else:
        report = (
            f"🚀 *【突破／強勢股前 20 檔】* ({today_str})\n"
            "今日無符合條件之突破或強勢標的。"
        )

    print(report)
    send_telegram_message(report, token, chat_id)


if __name__ == "__main__":
    main()
