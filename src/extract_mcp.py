"""从 MCP get_a_share_quotes 输出文件中提取总股本/PE/PB → SQLite"""
import json, sqlite3, os, glob
from config import DB_PATH, DATA_DIR

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

# 找所有 MCP 输出文件
mcp_dir = os.path.expanduser(
    r"~/.claude/projects/C--Users-14603-Desktop-for-claude/*/tool-results"
)
files = glob.glob(os.path.join(mcp_dir, "mcp-stock-sdk-get_a_share_quotes-*.txt"))
files = sorted(files, key=os.path.getmtime, reverse=True)
print(f"找到 {len(files)} 个 MCP 输出文件")

inserted = 0
for fp in files:
    print(f"处理: {os.path.basename(fp)[:60]}...")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  跳过: {e}")
        continue

    for item in data:
        code = item.get("code", "")
        name = item.get("name", "")
        ts = item.get("totalShares")
        fs = item.get("circulatingShares")
        pe = item.get("peDynamic") or item.get("pe")
        pb = item.get("pb")

        if not code or not ts:
            continue

        conn.execute(
            """INSERT OR REPLACE INTO stock_info
               (stock_code, stock_name, total_shares, float_shares,
                market_cap, pe_dynamic, pb, update_date)
               VALUES (?, ?, ?, ?, NULL, ?, ?, date('now'))""",
            (code, name, int(ts), int(fs) if fs else None, pe, pb),
        )
        inserted += 1

conn.commit()
print(f"\n共写入 {inserted} 条 → {DB_PATH}")

# 覆盖度检查
with open(os.path.join(DATA_DIR, "csi300_stocks.json"), "r", encoding="utf-8") as f:
    csi300 = json.load(f)
target_codes = {s["code"] for s in csi300}
cur = conn.execute("SELECT stock_code FROM stock_info")
have_codes = {r[0] for r in cur}
missing = target_codes - have_codes
print(f"已覆盖: {len(have_codes & target_codes)}/{len(target_codes)}")
if missing:
    print(f"缺失 {len(missing)} 只: {sorted(missing)[:20]}...")

conn.close()
