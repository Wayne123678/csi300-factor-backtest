"""基本面数据：直连东方财富API获取总股本/PE/PB → stock_info"""
import requests, sqlite3, time, random, os
from config import STOCK_POOL, DATA_DIR, DB_PATH

os.makedirs(DATA_DIR, exist_ok=True)
conn = sqlite3.connect(DB_PATH)
conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_info (
        stock_code    TEXT PRIMARY KEY,
        stock_name    TEXT,
        total_shares  INTEGER,
        float_shares  INTEGER,
        market_cap    REAL,
        pe_dynamic    REAL,
        pb            REAL,
        update_date   TEXT
    )
""")

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SES = requests.Session()
SES.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
})

BATCH = len(STOCK_POOL)
fetched, skipped, failed = 0, 0, 0

for i, (code, name) in enumerate(STOCK_POOL, 1):
    cur = conn.execute("SELECT COUNT(*) FROM stock_info WHERE stock_code=?", (code,))
    if cur.fetchone()[0] > 0:
        skipped += 1; continue

    result = "FAIL"
    for att in range(1, 4):
        try:
            r = SES.get(URL, params={
                "sortColumns": "TRADE_DATE", "sortTypes": "-1",
                "pageSize": "1", "pageNumber": "1",
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "SECURITY_CODE,TOTAL_SHARES,FREE_SHARES_A,PE_LAR,PB_MRQ,TOTAL_MARKET_CAP",
                "source": "WEB", "client": "WEB",
                "filter": f'(SECURITY_CODE="{code}")',
            }, timeout=20)
            d = r.json()
            if not d.get("success"):
                break
            data = d["result"]["data"]
            if not data:
                result = "EMPTY"; break

            row = data[0]
            ts = int(float(row["TOTAL_SHARES"])) if row.get("TOTAL_SHARES") else None
            fs = int(float(row["FREE_SHARES_A"])) if row.get("FREE_SHARES_A") else None
            pe = float(row["PE_LAR"]) if row.get("PE_LAR") else None
            pb = float(row["PB_MRQ"]) if row.get("PB_MRQ") else None

            if ts is None:
                break

            conn.execute(
                """INSERT OR REPLACE INTO stock_info
                   (stock_code, stock_name, total_shares, float_shares,
                    market_cap, pe_dynamic, pb, update_date)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, date('now'))""",
                (code, name, ts, fs, pe, pb),
            )
            conn.commit()
            fetched += 1; result = "OK"
            break
        except Exception:
            if att < 3: time.sleep(2)

    if i % 20 == 0 or i == BATCH:
        print(f"  [{i}/{BATCH}] 新拉{fetched} 跳过{skipped} 失败{failed}", flush=True)
    if result == "FAIL": failed += 1
    time.sleep(0.5 + random.uniform(0, 0.3))

cur = conn.execute("SELECT COUNT(*) FROM stock_info")
print(f"\n共 {cur.fetchone()[0]} 只 → {DB_PATH}")
conn.close()
