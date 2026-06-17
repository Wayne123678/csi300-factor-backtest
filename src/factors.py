import sqlite3
import pandas as pd
import os
from config import DB_PATH

db_path = DB_PATH

# ── 从 SQLite 读取全表 ─────────────────────────────────
conn = sqlite3.connect(db_path)
df = pd.read_sql("SELECT * FROM daily_price ORDER BY stock_code, trade_date", conn)

# 读取总股本数据（每只股票一个值）
info_df = pd.read_sql("SELECT stock_code, total_shares FROM stock_info", conn)
info_df = info_df.set_index("stock_code")

# 估值因子：从 daily_valuation 表 JOIN 真实历史PE/PB
val_df = pd.read_sql("SELECT stock_code, trade_date, pe_ttm, pe_dynamic, pb FROM daily_valuation", conn)
df = df.merge(val_df, on=["stock_code", "trade_date"], how="left")
df["pe"] = df["pe_dynamic"].fillna(df["pe_ttm"])  # 优先动态PE，fallback TTM
df.drop(columns=["pe_ttm", "pe_dynamic"], inplace=True)

# trade_date 转成 datetime，确保排序正确
df["trade_date"] = pd.to_datetime(df["trade_date"])

# ── 按股票分组算因子 ──────────────────────────────────
gp = df.groupby("stock_code", group_keys=False)

# 日收益率（中间变量）
df["ret"] = gp["close"].transform(lambda x: x.pct_change(1))

# 动量因子
df["mom_5"]  = gp["close"].transform(lambda x: x.pct_change(5))
df["mom_20"] = gp["close"].transform(lambda x: x.pct_change(20))
df["mom_60"] = gp["close"].transform(lambda x: x.pct_change(60))

# 反转因子（1日反转）：今天涨 → 明天看跌，今天跌 → 明天看涨
df["rev_1"] = -df["ret"]

# 波动率因子
df["vol_20"] = gp["ret"].transform(lambda x: x.rolling(20).std())

# 换手率因子
df["turn_5"] = gp["turnover"].transform(lambda x: x.rolling(5).mean())

# 成交量比
df["vol_ratio_5"] = gp["volume"].transform(
    lambda x: x / x.rolling(5).mean()
)

# 振幅因子
df["amp_5"] = gp["amplitude"].transform(lambda x: x.rolling(5).mean())

# 市值因子：收盘价 × 总股本 → 取对数（茅台2万亿 vs 京东方千亿，差20倍，不取ln会压扁其他因子）
import numpy as np
total_shares_map = info_df["total_shares"].to_dict()
df["market_cap"] = df.apply(
    lambda row: row["close"] * total_shares_map.get(row["stock_code"], 0)
    if total_shares_map.get(row["stock_code"], 0) > 0 else None,
    axis=1,
)
df["size"] = np.log(df["market_cap"])  # 对数市值

# ── 清理 ──────────────────────────────────────────────
df.drop(columns=["ret"], inplace=True)
df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")

# ── 写入 factors 表 ──────────────────────────────────
factors_df = df[[
    "stock_code", "trade_date",
    "mom_5", "mom_20", "mom_60", "rev_1",
    "vol_20",
    "turn_5", "vol_ratio_5", "amp_5",
    "size", "pe", "pb",
]].copy()

conn.execute("DROP TABLE IF EXISTS factors")
conn.execute("""
    CREATE TABLE factors (
        stock_code  TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        mom_5       REAL,
        mom_20      REAL,
        mom_60      REAL,
        rev_1       REAL,
        vol_20      REAL,
        turn_5      REAL,
        vol_ratio_5 REAL,
        amp_5       REAL,
        size        REAL,
        pe          REAL,
        pb          REAL,
        PRIMARY KEY (stock_code, trade_date)
    )
""")

factors_df.to_sql("factors", conn, if_exists="append", index=False)

# ── 统计 ──────────────────────────────────────────────
cur = conn.execute("""
    SELECT stock_code, COUNT(*) AS n, MIN(trade_date), MAX(trade_date)
    FROM factors
    WHERE mom_5 IS NOT NULL
    GROUP BY stock_code
    ORDER BY stock_code
""")
print(f"{'代码':<10} {'有效天数':<10} {'起始':<12} {'截止'}")
print("-" * 46)
for row in cur:
    print(f"{row[0]:<10} {row[1]:<10} {row[2]:<12} {row[3]}")

# 各因子概览
print("\n── 因子概览（全部股票合并）──")
for col in ["mom_5", "mom_20", "mom_60", "rev_1", "vol_20", "turn_5", "vol_ratio_5", "amp_5", "size", "pe", "pb"]:
    cur = conn.execute(f"SELECT COUNT(*), AVG({col}), MIN({col}), MAX({col}) FROM factors WHERE {col} IS NOT NULL")
    cnt, avg_, min_, max_ = cur.fetchone()
    if cnt == 0:
        print(f"  {col:<14} {'—':>5}  无有效数据")
    else:
        print(f"  {col:<14} {cnt:>5} 行  均值{avg_:>8.4f}  最小{min_:>8.4f}  最大{max_:>8.4f}")

cur = conn.execute("SELECT COUNT(*) FROM factors")
total = cur.fetchone()[0]
print(f"\nfactors 表共 {total} 行（含 NaN 的边界天数）")
print(f"数据库: {db_path}")

conn.commit()
conn.close()
