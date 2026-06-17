"""
多因子合成：Z-score 标准化 + 三种加权 → 综合得分
=====================================================
方法A：等权    score = z1 + z2 + z3
方法B：ICIR加权 score = Σ(w_i × z_i),  w_i = |ICIR_i| / Σ|ICIR|
方法C：IC均值加权 score = Σ(w_i × z_i), w_i = |IC_mean_i| / Σ|IC_mean|

核心问题：因子量级不同(mom~0.01, size~27)，不标准化无法加权
解决：每月组内Z-score → 均值为0标准差为1 → 量级一致
"""

import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from config import (
    SYNTHESIS_FACTORS, DB_PATH, PRIMARY_FWD, IC_DECAY_HORIZONS,
    REBALANCE_FREQ, PERIOD_LABEL,
    MIN_STOCKS_PER_PERIOD, MIN_IC_MONTHS, N_QUANTILES, LABELS_3,
    RATING_THRESHOLDS, MAD_N,
)

db_path = DB_PATH

# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def winsorize_mad(df, cols):
    df = df.copy()
    for col in cols:
        median = df[col].median()
        mad_val = (df[col] - median).abs().median()
        if mad_val == 0:
            continue
        df[col] = df[col].clip(median - MAD_N * mad_val, median + MAD_N * mad_val)
    return df


def compute_single_ic(monthly, factor, fwd_col):
    """算单个因子的 IC 汇总（用于获取权重）"""
    ics = []
    for _, grp in monthly.groupby("period"):
        valid = grp[[factor, fwd_col]].dropna()
        if len(valid) < 3:
            continue
        ic, _ = spearmanr(valid[factor], valid[fwd_col])
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < MIN_IC_MONTHS:
        return None
    im, is_ = np.mean(ics), np.std(ics, ddof=1)
    return {"ic_mean": im, "ic_std": is_, "icir": im / is_ if is_ > 0 else 0, "n": len(ics)}


def compute_ic(monthly, factors, fwd_col):
    """多因子 IC 汇总"""
    results = {}
    for factor in factors:
        ics, pvals = [], []
        for _, grp in monthly.groupby("period"):
            valid = grp[[factor, fwd_col]].dropna()
            if len(valid) < 3:
                continue
            ic, p = spearmanr(valid[factor], valid[fwd_col])
            if not np.isnan(ic):
                ics.append(ic)
                pvals.append(p)
        if len(ics) < MIN_IC_MONTHS:
            continue
        results[factor] = {
            "ic_mean": np.mean(ics),
            "ic_std": np.std(ics, ddof=1),
            "icir": np.mean(ics) / np.std(ics, ddof=1) if np.std(ics, ddof=1) > 0 else 0,
            "n": len(ics),
            "p_median": np.median(pvals),
            "sig_5pct": np.mean(np.array(pvals) < 0.05),
        }
    return results


def compute_layers(monthly, score_col, fwd_col):
    """分层回测"""
    top_rets, mid_rets, bot_rets = [], [], []
    for _, grp in monthly.groupby("period"):
        valid = grp[[score_col, fwd_col]].dropna()
        if len(valid) < MIN_STOCKS_PER_PERIOD:
            continue
        try:
            valid = valid.copy()
            valid["group"] = pd.qcut(
                valid[score_col].rank(method="first"), N_QUANTILES,
                labels=LABELS_3,
            )
        except ValueError:
            continue
        top_rets.append(valid[valid["group"] == "top"][fwd_col].mean())
        mid_rets.append(valid[valid["group"] == "mid"][fwd_col].mean())
        bot_rets.append(valid[valid["group"] == "bottom"][fwd_col].mean())

    if len(top_rets) < MIN_IC_MONTHS:
        return None
    spreads = np.array(top_rets) - np.array(bot_rets)
    return {
        "top": np.mean(top_rets), "mid": np.mean(mid_rets),
        "bot": np.mean(bot_rets), "spread": np.mean(spreads),
        "win_rate": np.mean(spreads > 0),
    }


# ═══════════════════════════════════════════════════════
# 读取 + 因子计算
# ═══════════════════════════════════════════════════════
conn = sqlite3.connect(db_path)
df = pd.read_sql("""
    SELECT f.*, d.close
    FROM factors f
    JOIN daily_price d ON f.stock_code = d.stock_code AND f.trade_date = d.trade_date
    ORDER BY f.stock_code, f.trade_date
""", conn)
conn.close()

df["trade_date"] = pd.to_datetime(df["trade_date"])

# MAD 去极值
df = winsorize_mad(df, SYNTHESIS_FACTORS)

# 未来收益率
gp = df.groupby("stock_code")
FWD_COLS = {}
for days in IC_DECAY_HORIZONS:
    col = f"fwd_ret_{days}"
    df[col] = gp["close"].transform(lambda x, d=days: x.pct_change(d).shift(-d))
    FWD_COLS[days] = col
PRIMARY_COL = FWD_COLS[PRIMARY_FWD]

# 月度快照
df["period"] = df["trade_date"].dt.to_period(REBALANCE_FREQ)
monthly = df.dropna(subset=[PRIMARY_COL]).sort_values("trade_date")
monthly = monthly.groupby(["period", "stock_code"]).last().reset_index()
monthly = monthly.groupby("period").filter(lambda g: len(g) >= MIN_STOCKS_PER_PERIOD)

# ═══════════════════════════════════════════════════════
# 获取各因子 IC（作为权重参考）
# ═══════════════════════════════════════════════════════
ic_info = compute_ic(monthly, SYNTHESIS_FACTORS, PRIMARY_COL)

# ═══════════════════════════════════════════════════════
# Z-score 标准化（每月组内独立算，避免 look-ahead bias）
# ═══════════════════════════════════════════════════════
z_cols = {}
for f in SYNTHESIS_FACTORS:
    z_col = f"{f}_z"
    monthly[z_col] = monthly.groupby("period")[f].transform(
        lambda x: (x - x.mean()) / x.std()
    )
    z_cols[f] = z_col

# ═══════════════════════════════════════════════════════
# 三种加权 → 综合得分
# ═══════════════════════════════════════════════════════

# 方法A：等权
monthly["score_eq"] = sum(monthly[z_cols[f]] for f in SYNTHESIS_FACTORS)

# 方法B：ICIR 加权
icir_total = sum(abs(ic_info[f]["icir"]) for f in SYNTHESIS_FACTORS)
monthly["score_icir"] = sum(
    abs(ic_info[f]["icir"]) / icir_total * monthly[z_cols[f]]
    for f in SYNTHESIS_FACTORS
)

# 方法C：IC均值加权
ic_total = sum(abs(ic_info[f]["ic_mean"]) for f in SYNTHESIS_FACTORS)
monthly["score_ic"] = sum(
    abs(ic_info[f]["ic_mean"]) / ic_total * monthly[z_cols[f]]
    for f in SYNTHESIS_FACTORS
)

# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════
n_periods = monthly["period"].nunique()

# ── 表1：因子权重 ──────────────────────────────────────
print("=" * 80)
print(f"多因子合成 — {PERIOD_LABEL} | 未来{PRIMARY_FWD}日 | {n_periods}个周期 | {len(SYNTHESIS_FACTORS)}因子")
print("=" * 80)
print(f"\n{'因子':<14} {'IC均值':>8} {'ICIR':>8} {'IC方向':>8} {'方向处理':>20}")
print("-" * 60)
for f in SYNTHESIS_FACTORS:
    info = ic_info[f]
    direction = "正向" if info["ic_mean"] > 0 else "反向"
    handling = "z-score直接加" if info["ic_mean"] > 0 else "z-score取负号"
    print(f"{f:<14} {info['ic_mean']:>8.4f} {info['icir']:>8.3f} {direction:>8} {handling:>20}")

print(f"\n[!] 当前样本中 size IC为正(大市值收益更高)，z-score直接加。")
print(f"    扩到全市场后 size IC 大概率转负，届时需对 size_z 取负号。")

# ── 表2：合成因子 IC ──────────────────────────────────
print("\n" + "=" * 80)
print("合成因子 IC 分析（三种加权 vs 单因子）")
print("=" * 80)
scores = {
    "等权":    "score_eq",
    "ICIR加权": "score_icir",
    "IC均值加权": "score_ic",
}

all_targets = {**{f"单因子-{f}": f for f in SYNTHESIS_FACTORS}, **scores}
composite_ic = {}

print(f"{'方法':<16} {'IC均值':>8} {'IC_std':>8} {'ICIR':>8} {'P中位':>8} {'<0.05':>6} {'N':>5}")
print("-" * 68)

for label, col in all_targets.items():
    if label.startswith("单因子-"):
        factor = col  # col = factor name like "mom_20"
        info = {"ic_mean": 0, "ic_std": 0, "icir": 0, "n": 0, "p_median": 0, "sig_5pct": 0}
        ics, pvals = [], []
        for _, grp in monthly.groupby("period"):
            valid = grp[[factor, PRIMARY_COL]].dropna()
            if len(valid) < 3:
                continue
            ic, p = spearmanr(valid[factor], valid[PRIMARY_COL])
            if not np.isnan(ic):
                ics.append(ic)
                pvals.append(p)
        if len(ics) >= MIN_IC_MONTHS:
            info = {
                "ic_mean": np.mean(ics), "ic_std": np.std(ics, ddof=1),
                "icir": np.mean(ics)/np.std(ics, ddof=1) if np.std(ics, ddof=1)>0 else 0,
                "n": len(ics), "p_median": np.median(pvals),
                "sig_5pct": np.mean(np.array(pvals) < 0.05),
            }
    else:
        # 合成得分列
        info_ = compute_single_ic(monthly, col, PRIMARY_COL)
        info = info_ if info_ else {"ic_mean": 0, "ic_std": 0, "icir": 0, "n": 0, "p_median": 0, "sig_5pct": 0}
    composite_ic[label] = info
    print(f"{label:<16} {info['ic_mean']:>8.4f} {info['ic_std']:>8.4f} {info['icir']:>8.3f} "
          f"{info.get('p_median',0):>8.3f} {info.get('sig_5pct',0):>5.0%} {info['n']:>5}")

# ── 表3：分层回测对比 ──────────────────────────────────
print("\n" + "=" * 80)
print("分层回测对比（Top - Bottom，月频调仓）")
print("=" * 80)
print(f"{'方法':<16} {'Top':>8} {'Mid':>8} {'Bot':>8} {'spread':>8} {'smart':>8} {'胜率':>6}")
print("-" * 68)

best_label, best_spread = "", -999
for label, col in all_targets.items():
    lr = compute_layers(monthly, col, PRIMARY_COL)
    if lr is None:
        continue
    # 单因子方向调整
    if label.startswith("单因子-"):
        factor = col
        ic_dir = ic_info[factor]["ic_mean"]
        smart = lr["spread"] if ic_dir > 0 else -lr["spread"]
    else:
        # 合成因子的 IC 方向
        ci = composite_ic[label]["ic_mean"]
        smart = lr["spread"] if ci > 0 else -lr["spread"]

    print(f"{label:<16} {lr['top']:>7.2%} {lr['mid']:>7.2%} {lr['bot']:>7.2%} "
          f"{lr['spread']:>7.2%} {smart:>7.2%} {lr['win_rate']:>5.0%}")
    if smart > best_spread:
        best_spread = smart
        best_label = label

# ── 表4：综合评级 ──────────────────────────────────────
print("\n" + "=" * 80)
print("综合评级（ICIR + 胜率 + smart方向）")
print("=" * 80)
print(f"{'方法':<16} {'ICIR':>8} {'smart':>8} {'胜率':>6} {'评级':>6} {'vs最佳单因子':>16}")
print("-" * 68)

best_single_spread = 0
for f in SYNTHESIS_FACTORS:
    lr = compute_layers(monthly, f, PRIMARY_COL)
    if lr:
        s = lr["spread"] if ic_info[f]["ic_mean"] > 0 else -lr["spread"]
        best_single_spread = max(best_single_spread, s)

for label in {**scores}:
    ci = composite_ic.get(label, {})
    lr = compute_layers(monthly, scores[label], PRIMARY_COL)
    if ci is None or lr is None:
        continue

    abs_icir = abs(ci.get("icir", 0))
    smart = lr["spread"] if ci.get("ic_mean", 0) > 0 else -lr["spread"]
    wr = lr.get("win_rate", 0)
    smart_pos = smart > 0

    a_cfg = RATING_THRESHOLDS["A"]
    b_cfg = RATING_THRESHOLDS["B"]
    c_cfg = RATING_THRESHOLDS["C"]

    if abs_icir > a_cfg["icir"] and wr > a_cfg["win_rate"] and smart_pos:
        grade = "[A]"
    elif abs_icir > b_cfg["icir"] and wr > b_cfg["win_rate"]:
        grade = "[B]"
    elif abs_icir > c_cfg["icir"]:
        grade = "[C]"
    else:
        grade = "[--]"

    improvement = smart - best_single_spread
    imp_str = f"+{improvement:.2%}" if improvement > 0 else f"{improvement:.2%}"
    print(f"{label:<16} {ci.get('icir',0):>8.3f} {smart:>7.2%} {wr:>5.0%} {grade:>6} {imp_str:>16}")

print(f"\n最佳单因子 smart_spread: {best_single_spread:.2%}")
print(f"最佳合成方法: {best_label} (smart={best_spread:.2%})")
n_stocks = df["stock_code"].nunique()
print(f"[!] {n_stocks}只股票+{n_periods}个月，基于CSI 300分析。")
