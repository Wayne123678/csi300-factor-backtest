"""项目公共配置 — 股票池、因子列表、路径
数据源：
  daily_price: build_db.py → 腾讯API(OHLCV) + 本地计算(pct/change/amp/turnover)
  daily_valuation: build_valuation.py → 东方财富数据中心API(PE_TTM/PE_LAR/PB)
  stock_info: fundamentals.py → 东方财富数据中心API(总股本/PE/PB) 或 extract_mcp.py(MCP兜底)
注意：amount列不可用(push2his API Python侧不可达)，不影响任何因子
"""

import os
import json

# ── 路径 ──────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(script_dir)
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "stocks.db")  # small模式用旧路径

# ── 股票池（沪深300成分股）───────────────────────────
_csi300_path = os.path.join(DATA_DIR, "csi300_stocks.json")
if not os.path.exists(_csi300_path):
    raise FileNotFoundError(
        f"请先生成沪深300成分股列表:\n"
        f"  python -c \"import akshare as ak, json; "
        f"df=ak.index_stock_cons_csindex('000300'); "
        f"pool=[{{'code':str(c),'name':str(n)}} for c,n in "
        f"zip(df['成分券代码'],df['成分券名称'])]; "
        f"json.dump(pool, open(r'{_csi300_path}','w',encoding='utf-8'), ensure_ascii=False); "
        f"print(f'{{len(pool)}} stocks')\""
    )

with open(_csi300_path, "r", encoding="utf-8") as f:
    STOCK_POOL = [(s["code"], s["name"]) for s in json.load(f)]

# ── 因子列表 ──────────────────────────────────────────
FACTORS = [
    "mom_5", "mom_20", "mom_60", "rev_1",
    "vol_20", "turn_5", "vol_ratio_5", "amp_5", "size",
    "pe", "pb",   # 估值因子（最新快照，非历史逐日PE/PB）
]

# 多因子合成阵容
SYNTHESIS_FACTORS = ["size", "vol_20", "amp_5", "pe", "pb"]

# ── 数据参数 ──────────────────────────────────────────
START_DATE = "20240101"
END_DATE = "20250615"

# ── 去极值参数 ────────────────────────────────────────
WINSOR_PCT = (0.01, 0.99)    # 分位数截尾
MAD_N = 5                     # MAD 标准差倍数

# ── 因子分析参数 ──────────────────────────────────────
# 调仓与展望周期（交易日）
PRIMARY_FWD = 20               # 主分析：未来N日收益（月频≈20日）
IC_DECAY_HORIZONS = [5, 10, 20, 40, 60]  # IC衰减曲线使用的展望周期
# 参数中的"N日收益"的N对应因子命名（mom_5=5, mom_20=20, …），不在此配置

# 调仓频率
REBALANCE_FREQ = "M"          # pandas offset alias: M=月频, W=周频
PERIOD_LABEL = "月频调仓"     # 用于输出表格

# 统计约束
MIN_STOCKS_PER_PERIOD = 5     # 每个调仓周期最少股票数
MIN_IC_MONTHS = 3             # 最少需要的月份数（IC分析）
CORR_HIGH_THRESHOLD = 0.7     # 高相关阈值：|r|超过此值标记警告

# 分组回测
N_QUANTILES = 3               # 分层数：3=top/mid/bottom
LABELS_3 = ["bottom", "mid", "top"]

# 综合评级阈值
RATING_THRESHOLDS = {
    "A": {"icir": 0.3, "win_rate": 0.6, "smart_positive": True},
    "B": {"icir": 0.15, "win_rate": 0.5},
    "C": {"icir": 0.1},
}
