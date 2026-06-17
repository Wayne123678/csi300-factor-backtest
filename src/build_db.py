"""
日线数据：腾讯源(OHLCV) + 新浪源(额外列) + 本地计算补全
=========================================================
盘后可用，300只约2分钟。
"""
import requests, sqlite3, json, time, random, os
from config import STOCK_POOL, DATA_DIR, DB_PATH, START_DATE, END_DATE

os.makedirs(DATA_DIR, exist_ok=True)
conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_price (
        stock_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, amount REAL,
        amplitude REAL, pct_change REAL, change REAL, turnover REAL,
        PRIMARY KEY (stock_code, trade_date)
    )
""")
conn.commit()

def sym(code):
    return ("sh" if code.startswith(("60","68")) else "sz") + code

TX_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData/getKLineData"
TX_SES = requests.Session()
TX_SES.headers.update({"Referer": "http://web.ifzq.gtimg.cn/"})
SINA_SES = requests.Session()

start_d = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}"
end_d = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}"

BATCH = len(STOCK_POOL)
fetched, skipped, failed = 0, 0, 0
failed_list = []

print(f"共 {BATCH} 只待拉取，数据源：腾讯财经（盘后可用）")

# ═══════════════════════════════════════════════════════
# 第1步：拉取 OHLCV
# ═══════════════════════════════════════════════════════
for i, (code, name) in enumerate(STOCK_POOL, 1):
    cur = conn.execute("SELECT COUNT(*) FROM daily_price WHERE stock_code=?", (code,))
    if cur.fetchone()[0] > 0:
        skipped += 1
        continue

    s = sym(code)
    result, n_rows = "FAIL", 0
    tx_data = None

    for att in range(1, 4):
        try:
            r = TX_SES.get(TX_URL, params={"param": f"{s},day,{start_d},{end_d},400,qfq"}, timeout=15)
            d = r.json()
            tx_data = d.get("data", {}).get(s, {}).get("qfqday", [])
            if not tx_data:
                r2 = TX_SES.get(TX_URL, params={"param": f"{s},day,{start_d},{end_d},400,"}, timeout=15)
                tx_data = r2.json().get("data", {}).get(s, {}).get("day", [])
            break
        except:
            if att < 3: time.sleep(1)

    if not tx_data:
        print(f"  [{i}/{BATCH}] {code} {name} FAIL", flush=True)
        failed += 1; failed_list.append(code)
        time.sleep(0.3); continue

    # 新浪源补 amount/turnover（失败不致命）
    sina_map = {}
    try:
        r = SINA_SES.get(SINA_URL, params={"symbol": s, "scale": "240", "datalen": "400"}, timeout=10)
        if r.text.startswith("[") and len(r.text) > 10:
            for k in json.loads(r.text):
                d_ = k.get("day", "")
                sina_map[d_] = {
                    "amount": float(k["volume"]) if k.get("volume") else None,
                    "turnover": float(k.get("turnover", 0)) if k.get("turnover") else None,
                }
    except:
        pass

    rows = []
    for line in tx_data:
        if len(line) < 6: continue
        try:
            date = line[0]
            op, cl, hi, lo = float(line[1]), float(line[2]), float(line[3]), float(line[4])
            vol = int(float(line[5]))
            sina = sina_map.get(date, {})
            rows.append((code, date, op, hi, lo, cl, vol,
                         sina.get("amount"), None, None, None,
                         sina.get("turnover")))
        except (ValueError, IndexError):
            continue

    if rows:
        conn.executemany(
            """INSERT OR IGNORE INTO daily_price
               (stock_code,trade_date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        n_rows = len(rows)
        fetched += 1; result = "OK"

    has_sina = "+新浪" if sina_map else ""
    print(f"  [{i}/{BATCH}] {code} {name} {result} {n_rows}条 {has_sina}", flush=True)
    if result == "FAIL": failed += 1; failed_list.append(code)
    time.sleep(0.2 + random.uniform(0, 0.2))

# ═══════════════════════════════════════════════════════
# 第2步：从 OHLCV 计算 pct_change / change / amplitude / turnover
# ═══════════════════════════════════════════════════════
print("\n补全计算列 (pct_change / change / amplitude / turnover)...")
cur = conn.execute("SELECT stock_code, trade_date, open, high, low, close FROM daily_price ORDER BY stock_code, trade_date")
rows_all = cur.fetchall()

from itertools import groupby
updated = 0
for code, group in groupby(rows_all, key=lambda r: r[0]):
    prev_close = None
    batch = []
    for row in group:
        _, date, op, hi, lo, cl = row
        if prev_close is not None and prev_close != 0:
            batch.append((cl / prev_close - 1, cl - prev_close, (hi - lo) / prev_close, code, date))
        prev_close = cl
        if len(batch) >= 500:
            conn.executemany(
                "UPDATE daily_price SET pct_change=?, change=?, amplitude=? WHERE stock_code=? AND trade_date=?",
                batch)
            updated += len(batch)
            batch.clear()
    if batch:
        conn.executemany(
            "UPDATE daily_price SET pct_change=?, change=?, amplitude=? WHERE stock_code=? AND trade_date=?",
            batch)
        updated += len(batch)
conn.commit()

# ── 计算 turnover = volume / total_shares（交易所定义，非估算）──
print("补全 turnover (volume / total_shares)...")
ts_map = {r[0]: r[1] for r in conn.execute("SELECT stock_code, total_shares FROM stock_info")}
cur = conn.execute("SELECT stock_code, trade_date, volume FROM daily_price WHERE turnover IS NULL")
rows = []
for code, date, vol in cur:
    ts = ts_map.get(code)
    if ts and ts > 0 and vol:
        rows.append((vol / ts * 100, code, date))
for i in range(0, len(rows), 500):
    conn.executemany("UPDATE daily_price SET turnover=? WHERE stock_code=? AND trade_date=?", rows[i:i+500])
conn.commit()

# ═══════════════════════════════════════════════════════
# 收尾
# ═══════════════════════════════════════════════════════
cur = conn.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM daily_price")
s, t = cur.fetchone()
cur = conn.execute("SELECT COUNT(*) FROM daily_price WHERE pct_change IS NOT NULL")
pct = cur.fetchone()[0]
cur = conn.execute("SELECT COUNT(*) FROM daily_price WHERE turnover IS NOT NULL")
turn = cur.fetchone()[0]
print(f"\n共 {s} 只, {t} 条 → {DB_PATH}")
print(f"  pct_change/change/amplitude: {pct}/{t} ({pct/t*100:.0f}%)")
print(f"  turnover: {turn}/{t} ({turn/t*100:.0f}%)")
print(f"  amount: 不可用（push2his API Python侧不可达，不影响任何因子）")
if failed_list:
    print(f"[!] {len(failed_list)} 只失败")
conn.close()
