from datetime import datetime
import os
import re
import requests
import pandas as pd
import yfinance as yf


def get_env_config() -> tuple[str, str]:
    """從環境變數取得 Telegram Bot Token 與 Chat ID。"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 環境變數")
    return token, chat_id


def send_telegram_message(text: str) -> None:
    """發送 Telegram 推播訊息（超過長度自動分段）。"""
    token, chat_id = get_env_config()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_len = 3800

    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] or ["（無推播內容）"]
    for chunk in chunks:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()


def get_all_tw_stocks(fallback_map: dict[str, str] | None = None) -> dict[str, str]:
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

    if not stock_map and fallback_map:
        return fallback_map

    return stock_map


def scan_top20_strong_stocks(
    stock_map: dict[str, str],
    top_n: int = 20,
    batch_size: int = 120,
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
                        candidates.append(
                            (
                                score,
                                f"• [{tag}] {code} {name}（+{pct_change:.1f}%｜收{last_close:.1f}｜量{vol_ratio:.1f}x）",
                            )
                        )
                except Exception:
                    continue
        except Exception:
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in candidates[:top_n]]


def monitor_portfolio_ma5(
    portfolio_tickers: dict[str, str],
    stock_map: dict[str, str] | None = None,
) -> list[str]:
    """
    庫存 5 日均線防守監控：檢查收盤價是否守穩 5MA 或跌破警戒。
    """
    lines: list[str] = []
    stock_map = stock_map or {}

    for ticker, custom_name in portfolio_tickers.items():
        code = ticker.split(".")[0]
        name = custom_name or stock_map.get(ticker, "")
        try:
            df = yf.download(
                ticker,
                period="1mo",
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if df is None or df.empty:
                lines.append(f"• {code} {name}：查無行情資料")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs(ticker, axis=1, level=0, drop_level=True) if ticker in df.columns.get_level_values(0) else df

            df = df.dropna(subset=["Close"])
            if len(df) < 5:
                lines.append(f"• {code} {name}：資料不足 5 日")
                continue

            close = df["Close"].astype(float)
            last_close = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
            ma5 = float(close.rolling(5).mean().iloc[-1])
            pct_change = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

            if last_close < ma5:
                status = "⚠️ 跌破5MA警戒"
            else:
                status = "✅ 守穩5MA"

            lines.append(
                f"• {status} {code} {name}（收{last_close:.1f} / 5MA {ma5:.1f}｜{pct_change:+.1f}%）"
            )
        except Exception as e:
            lines.append(f"• {code} {name}：查詢失敗（{e}）")

    return lines


def main() -> None:
    # 依你的實際庫存標的調整此字典（可自行增減代號與名稱）
    portfolio_tickers = {
        "2330.TW": "台積電",
    }

    all_stocks = get_all_tw_stocks()
    portfolio_lines = monitor_portfolio_ma5(portfolio_tickers, all_stocks)
    top20_strong = scan_top20_strong_stocks(all_stocks, top_n=20)

    portfolio_section = "🛡️【庫存 5 日線防守監控】\n" + (
    "\n".join(portfolio_lines) if portfolio_lines else "（目前無設定庫存標的）"
)

if top20_strong:
    strong_section = "🚀【突破／強勢股前 20 檔】\n" + "\n".join(top20_strong)
else:
    strong_section = "🚀【突破／強勢股前 20 檔】\n今日無符合條件之突破或強勢標的。"

report = f"{portfolio_section}\n\n{strong_section}"
print(f"成功取得上市櫃股票數：{len(all_stocks)} 檔")
print(report)
send_telegram_message(report
if __name__ == "__main__":
    main()
