"""从MCP输出文件提取stock_info → SQLite（个人工具，依赖本地MCP缓存）
=================================================================
他人clone项目不需要运行此脚本。stock_info已通过fundamentals.py（东方财富API直连）写入。
此脚本仅用于：当fundamentals.py的API不可用时，从Claude MCP的本地输出文件兜底。
用法: python extract_mcp.py [MCP输出目录路径]
      默认搜索 ~/.claude/projects/*/tool-results/
"""
import json, sqlite3, os, glob, sys
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

# MCP输出目录：命令行参数 > 环境变量 > 默认路径
if len(sys.argv) > 1:
    mcp_dir = sys.argv[1]
else:
    mcp_dir = os.environ.get(
        "MCP_OUTPUT_DIR",
        os.path.expanduser(r"~/.claude/projects/C--Users-14603-Desktop-for-claude/*/tool-results")
    )

files = sorted(glob.glob(os.path.join(mcp_dir, "mcp-stock-sdk-get_a_share_quotes-*.txt")))
files = [f for f in files if os.path.getmtime(f) > 0]  # 过滤无效文件

if not files:
    print("[!] 未找到MCP输出文件。请确认：")
    print(f"    1) MCP输出目录正确: {mcp_dir}")
    print(f"    2) 已运行过 MCP get_a_share_quotes 批量调用")
    print(f"  提示: 可直接用 fundamentals.py 替代，不依赖MCP。")
    sys.exit(1)

print(f"找到 {len(files)} 个MCP文件")
print(f"源目录: {mcp_dir}")
inserted = 0
for fp in files:
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
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
print(f"共写入 {inserted} 条 → {DB_PATH}")
conn.close()
