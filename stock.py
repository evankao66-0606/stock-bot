from datetime import datetime
import os
import pandas as pd
import requests
import yfinance as yf

# ----------------------------
# 權證專屬參數設定
# ----------------------------
N_HIGH = 20  # 創 20 日新高
VOL_MULT = 2.0  # 權證講求爆發力，量能倍數調高至 2.0 倍
RSI_LOW = 55  # 動能轉強區間下限
RSI_HIGH = 80  # 避免過度追高
MA_PERIOD = 20
MIN_VOL = 2000  # 權證標的需具備高流動性（5日均量 > 2000張）

# 從 GitHub Secrets 讀取 Telegram 憑證
TELEGRAM_BOT_TOKEN = os.environ.get("9156071043:AAEYKJJIA_BCc053CiKEm9wD3USTx3YiAUI")
CHAT_ID = os.environ.get("8302884631")

# 1. 候選掃描清單（可持續擴充您關注的熱門族群）
candidate_stocks = [
    # 半導體 / IC設計
    "2330.TW",
    "2303.TW",
    "2454.TW",
    "3711.TW",
    "2379.TW",
    "3034.TW",
    "2408.TW",
    "3443.TW",
    "3661.TW",
    # AI 伺服器 / 電子代工
    "2382.TW",
    "3231.TW",
    "2356.TW",
    "6669.TW",
    "2317.TW",
    "2308.TW",
    "3017.TW",
    "4938.TW",
    "2357.TW",
    "2376.TW",
    # 散熱 / PCB / 光電 / 零組件
    "3324.TW",
    "3037.TW",
    "2368.TW",
    "3008.TW",
    "3406.TW",
    "2327.TW",
    "2345.TW",
    # 航運 / 重電 / 金融 / 傳產權值
    "2603.TW",
    "2609.TW",
    "2615.TW",
    "2618.TW",
    "1519.TW",
    "1513.TW",
    "1504.TW",
    "1605.TW",
    "2002.TW",
    "2881.TW",
    "2882.TW",
    "2891.TW",
]

# 2. 您已買入權證所對應的「現股代碼」（監控現股是否跌破 5 日線）
# 例如：買了台積電與奇鋐的權證，請填入 ["2330.TW", "3017.TW"]
my_portfolio = []


def get_warrant_eligible_stocks():
  """從證交所開放資料取得目前可發行權證的上市股票代碼集合"""
  try:
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap46_L_1"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
      data = res.json()
      eligible = {
          f"{item['標的代號'].strip()}.TW" for item in data if "標的代號" in item
      }
      if len(eligible) > 0:
        print(f"✅ 成功取得證交所權證標的清單，共 {len(eligible)} 檔")
        return eligible
  except Exception as e:
    print(f"⚠️ 取得證交所權證清單失敗，改用內建清單過濾: {e}")
  return None


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
    except Exception as e:
      print(f"處理 {ticker} 時發生錯誤: {e}")

  if len(new_picks) > 0:
    df_picks = pd.DataFrame(new_picks).sort_values("量倍", ascending=False)
    msg_report += f"🔥 *【今日強勢權證標的】* ({len(df_picks)}檔)\n```\n"
    msg_report += df_picks.to_string(index=False)
    msg_report += "\n```\n\n"
  else:
    msg_report += "📊 *【今日強勢權證標的】*\n今日無符合爆量突破條件之權證標的。\n\n"

  # ==========================================
  # 第二部分：已買入權證之現股監控（跌破 5 日線出場警示）
  # ==========================================
  if len(my_portfolio) > 0:
    alerts = []
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
          drop_pct = ((latest_close - latest_ma5) / latest_ma5) * 100
          alerts.append({
              "代碼": ticker.replace(".TW", ""),
              "收盤": round(latest_close, 1),
              "MA5": round(latest_ma5, 1),
              "乖離%": round(drop_pct, 1),
          })
      except Exception as e:
        print(f"檢查持倉 {ticker} 時發生錯誤: {e}")

    if len(alerts) > 0:
      df_alerts = pd.DataFrame(alerts)
      msg_report += (
          f"⚠️ *【現股跌破5日線｜權證出場警示】* ({len(df_alerts)}檔)\n```\n"
      )
      msg_report += df_alerts.to_string(index=False)
      msg_report += "\n```\n"
    else:
      msg_report += "✅ *【持倉現股狀態】*\n所有持有權證之現股皆穩守 5 日線之上。"

  send_telegram_message(msg_report)


if __name__ == "__main__":
  run_warrant_radar()
