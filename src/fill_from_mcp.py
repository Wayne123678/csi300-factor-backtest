"""从MCP kline输出文件补全amount/turnover → daily_price（个人工具，依赖本地MCP缓存）
===============================================================================
他人clone不需要运行此脚本。build_db.py 已自动计算 turnover = volume/total_shares。
此脚本仅用于：从历史MCP kline调用中提取真实的amount（成交额）。
用法: python fill_from_mcp.py [MCP输出目录路径]
"""
import json, sqlite3, os, glob, sys
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

if len(sys.argv) > 1:
    mcp_dir = sys.argv[1]
else:
    mcp_dir = os.environ.get(
        "MCP_OUTPUT_DIR",
        os.path.expanduser(r"~/.claude/projects/C--Users-14603-Desktop-for-claude/*/tool-results")
    )

files = sorted(glob.glob(os.path.join(mcp_dir, "mcp-stock-sdk-get_history_kline-*.txt")))
files = [f for f in files if os.path.getmtime(f) > 0]

if not files:
    print("[!] 未找到MCP kline输出文件。")
    print(f"  提示: turnover已由build_db.py从volume/total_shares计算，amount不影响任何因子。")
    sys.exit(0)

print(f"找到 {len(files)} 个kline文件")
updated = 0
for fp in files:
    try:
        with open(fp, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        continue

    if not isinstance(raw, list):
        continue

    batch = []
    for k in raw:
        code = k.get("code", "")
        date = k.get("date", "")
        amt = k.get("amount")
        turnover = k.get("turnoverRate")
        if code and date and turnover is not None:
            batch.append((amt, turnover, code, date))

    if batch:
        conn.executemany(
            "UPDATE daily_price SET amount=?, turnover=? WHERE stock_code=? AND trade_date=?",
            batch)
        conn.commit()
        updated += len(batch)

print(f"共补全 {updated} 条 → {DB_PATH}")
conn.close()
