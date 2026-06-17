"""从 MCP get_history_kline 输出文件中提取 amount + turnover → 补全 daily_price"""
import json, sqlite3, os, glob
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

mcp_dir = os.path.expanduser(
    r"~/.claude/projects/C--Users-14603-Desktop-for-claude/*/tool-results"
)
files = sorted(glob.glob(os.path.join(mcp_dir, "mcp-stock-sdk-get_history_kline-*.txt")),
               key=os.path.getmtime, reverse=True)

print(f"找到 {len(files)} 个 kline 输出文件")
updated = 0
for fp in files:
    if "get_history_kline" not in fp:
        continue
    try:
        with open(fp, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except:
        continue

    # 格式：list of {date, open, close, ..., code, amount, turnoverRate}
    if not isinstance(raw, list):
        continue

    batch = []
    for k in raw:
        code = k.get("code", "")
        date = k.get("date", "")
        amt = k.get("amount")
        turnover = k.get("turnoverRate")
        if code and date and (amt is not None or turnover is not None):
            batch.append((amt, turnover, code, date))

    if batch:
        conn.executemany(
            "UPDATE daily_price SET amount=?, turnover=? WHERE stock_code=? AND trade_date=?",
            [(a, t, c, d) for a, t, c, d in batch])
        conn.commit()
        updated += len(batch)

print(f"共补全 {updated} 条 → {DB_PATH}")

# 统计覆盖
cur = conn.execute("SELECT COUNT(*) FROM daily_price")
total = cur.fetchone()[0]
cur = conn.execute("SELECT COUNT(*) FROM daily_price WHERE amount IS NOT NULL")
amt = cur.fetchone()[0]
cur = conn.execute("SELECT COUNT(*) FROM daily_price WHERE turnover IS NOT NULL")
turn = cur.fetchone()[0]
print(f"amount:  {amt}/{total} ({amt/total*100:.0f}%)")
print(f"turnover: {turn}/{total} ({turn/total*100:.0f}%)")
conn.close()
