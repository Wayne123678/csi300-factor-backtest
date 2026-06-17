"""历史PE/PB：直连东方财富数据中心API，绕过akshare限流"""
import requests, sqlite3, time, random, os
from config import STOCK_POOL, DATA_DIR, DB_PATH

os.makedirs(DATA_DIR, exist_ok=True)
conn = sqlite3.connect(DB_PATH)
conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_valuation (
        stock_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        pe_ttm REAL, pe_dynamic REAL, pb REAL,
        PRIMARY KEY (stock_code, trade_date)
    )
""")

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SES = requests.Session()
SES.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
})

BATCH = len(STOCK_POOL)
COOLDOWN = 15      # 每10只降温
COOLDOWN_SECS = 30

fetched, skipped, failed = 0, 0, 0

for i, (code, name) in enumerate(STOCK_POOL, 1):
    cur = conn.execute("SELECT COUNT(*) FROM daily_valuation WHERE stock_code=?", (code,))
    if cur.fetchone()[0] > 0:
        skipped += 1; continue

    result, n_rows = "FAIL", 0
    for att in range(1, 4):
        try:
            r = SES.get(URL, params={
                "sortColumns": "TRADE_DATE", "sortTypes": "-1",
                "pageSize": "5000", "pageNumber": "1",
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "SECURITY_CODE,TRADE_DATE,PE_TTM,PE_LAR,PB_MRQ",
                "source": "WEB", "client": "WEB",
                "filter": f'(SECURITY_CODE="{code}")',
            }, timeout=20)
            d = r.json()
            if not d.get("success"):
                break
            data = d["result"]["data"]
            if not data:
                result = "EMPTY"; break

            rows = [(code, row["TRADE_DATE"][:10],
                     row.get("PE_TTM"), row.get("PE_LAR"), row.get("PB_MRQ"))
                    for row in data]
            conn.executemany(
                "INSERT OR IGNORE INTO daily_valuation(stock_code,trade_date,pe_ttm,pe_dynamic,pb) VALUES(?,?,?,?,?)",
                rows)
            conn.commit()
            n_rows = len(rows); fetched += 1; result = "OK"
            break
        except Exception:
            if att < 3: time.sleep(2)

    print(f"  [{i}/{BATCH}] {code} {name} {result} {f'{n_rows}条' if n_rows else ''}", flush=True)
    if result == "FAIL": failed += 1

    if fetched > 0 and fetched % COOLDOWN == 0:
        print(f"  ~~~ {fetched}/{BATCH-skipped} 降温{COOLDOWN_SECS}s ~~~", flush=True)
        time.sleep(COOLDOWN_SECS)
    else:
        time.sleep(0.5 + random.uniform(0, 0.3))

cur = conn.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM daily_valuation")
s, t = cur.fetchone()
print(f"\n共 {s} 只, {t} 条 → {DB_PATH}")
conn.close()
