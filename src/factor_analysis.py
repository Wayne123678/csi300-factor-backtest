"""
因子有效性分析：IC + 分层回测
=============================
去极值：分位数 clip(1%,99%) vs MAD clip(±5σ)  两套结果对比
核心指标：
  IC      — 因子值与未来收益率的 Spearman 秩相关系数
  ICIR    — IC均值 / IC标准差（>0.3 可用，>0.5 好）
  多空spread — Top组收益 - Bottom组收益（纯因子alpha）
"""

import sqlite3, pandas as pd, numpy as np, os, warnings
from scipy.stats import spearmanr
warnings.filterwarnings("ignore")  # numpy空切片警告不影响结果
from config import (
    FACTORS, SYNTHESIS_FACTORS, DB_PATH,
    PRIMARY_FWD, IC_DECAY_HORIZONS, REBALANCE_FREQ, PERIOD_LABEL,
    MIN_STOCKS_PER_PERIOD, MIN_IC_MONTHS, CORR_HIGH_THRESHOLD,
    N_QUANTILES, LABELS_3, RATING_THRESHOLDS,
    WINSOR_PCT, MAD_N,
)

db_path = DB_PATH

# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def winsorize_percentile(df, cols):
    """分位数截尾"""
    lo_pct, hi_pct = WINSOR_PCT
    df = df.copy()
    for col in cols:
        lo, hi = df[col].quantile(lo_pct), df[col].quantile(hi_pct)
        df[col] = df[col].clip(lo, hi)
    return df


def winsorize_mad(df, cols):
    """MAD截尾"""
    df = df.copy()
    for col in cols:
        median = df[col].median()
        mad = (df[col] - median).abs().median()
        if mad == 0:
            continue
        lo = median - MAD_N * mad
        hi = median + MAD_N * mad
        df[col] = df[col].clip(lo, hi)
    return df


def build_monthly(df, fwd_col):
    """取每周期末快照"""
    df = df.copy()
    df["period"] = df["trade_date"].dt.to_period(REBALANCE_FREQ)
    m = df.dropna(subset=[fwd_col]).sort_values("trade_date")
    m = m.groupby(["period", "stock_code"]).last().reset_index()
    m = m.groupby("period").filter(lambda g: len(g) >= MIN_STOCKS_PER_PERIOD)
    return m


def compute_ic(monthly, factors, fwd_col):
    """逐周期算 Spearman IC + P值"""
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

        ic_mean = np.mean(ics)
        ic_std = np.std(ics, ddof=1)
        p_median = np.median(pvals)
        sig_5 = np.mean(np.array(pvals) < 0.05)
        sig_10 = np.mean(np.array(pvals) < 0.10)

        results[factor] = {
            "ic_mean": ic_mean, "ic_std": ic_std,
            "icir": ic_mean / ic_std if ic_std > 0 else 0,
            "n": len(ics),
            "p_median": p_median, "sig_5pct": sig_5, "sig_10pct": sig_10,
        }
    return results


def compute_layers(monthly, factors, fwd_col):
    """分层回测"""
    results = {}
    for factor in factors:
        top_rets, mid_rets, bot_rets = [], [], []
        for _, grp in monthly.groupby("period"):
            valid = grp[[factor, fwd_col]].dropna()
            if len(valid) < MIN_STOCKS_PER_PERIOD:
                continue
            try:
                valid = valid.copy()
                valid["group"] = pd.qcut(
                    valid[factor].rank(method="first"), N_QUANTILES,
                    labels=LABELS_3,
                )
            except ValueError:
                continue
            top_rets.append(valid[valid["group"] == "top"][fwd_col].mean())
            mid_rets.append(valid[valid["group"] == "mid"][fwd_col].mean())
            bot_rets.append(valid[valid["group"] == "bottom"][fwd_col].mean())

        if len(top_rets) < MIN_IC_MONTHS:
            continue
        spreads = np.array(top_rets) - np.array(bot_rets)
        results[factor] = {
            "top": np.mean(top_rets), "mid": np.mean(mid_rets),
            "bot": np.mean(bot_rets), "spread": np.mean(spreads),
            "win_rate": np.mean(spreads > 0),
        }
    return results


# ═══════════════════════════════════════════════════════
# 读取原始数据
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

# 未来收益率（所有可能用到的展望周期）
gp = df.groupby("stock_code")
FWD_COLS = {}
for days in IC_DECAY_HORIZONS:
    col = f"fwd_ret_{days}"
    df[col] = gp["close"].transform(lambda x, d=days: x.pct_change(d).shift(-d))
    FWD_COLS[days] = col

PRIMARY_COL = FWD_COLS[PRIMARY_FWD]

# ═══════════════════════════════════════════════════════
# 方案A：分位数去极值
# ═══════════════════════════════════════════════════════
df_a = winsorize_percentile(df, FACTORS)
monthly_a = build_monthly(df_a, PRIMARY_COL)
ic_a = compute_ic(monthly_a, FACTORS, PRIMARY_COL)
layer_a = compute_layers(monthly_a, FACTORS, PRIMARY_COL)

# ═══════════════════════════════════════════════════════
# 方案B：MAD 去极值
# ═══════════════════════════════════════════════════════
df_b = winsorize_mad(df, FACTORS)
monthly_b = build_monthly(df_b, PRIMARY_COL)
ic_b = compute_ic(monthly_b, FACTORS, PRIMARY_COL)
layer_b = compute_layers(monthly_b, FACTORS, PRIMARY_COL)

# ═══════════════════════════════════════════════════════
# 输出：三张表
# ═══════════════════════════════════════════════════════

n_periods = ic_b.get(FACTORS[0], {}).get("n", "?")
n_stocks = df["stock_code"].nunique()
PERIOD_INFO = f"{PERIOD_LABEL} | 未来{PRIMARY_FWD}日收益 | {n_periods}个周期 | {n_stocks}只 | 2024-01 ~ 2025-06"

def monotonicity(top, mid, bot, ic_direction=1):
    """判断因子分层收益的单调性
    ic_direction: 1=正向因子(IC>0)期望Top>Mid>Bot
                 -1=反向因子(IC<0)期望Bottom>Mid>Top
    """
    if ic_direction > 0:
        if top > mid > bot:
            return "[++] 严格"
        elif top > bot:
            return "[~~] T>B"
        else:
            return "[XX] 相反"
    else:
        if bot > mid > top:
            return "[++] 严格"
        elif bot > top:
            return "[~~] B>T"
        else:
            return "[XX] 相反"

def smart_spread(ic_mean, spread):
    """按IC方向调整spread：IC正→Top-Bottom，IC负→Bottom-Top"""
    if ic_mean > 0:
        return spread          # 正向因子：做多Top做空Bottom
    else:
        return -spread         # 反向因子：做多Bottom做空Top

# 公共表头
H = (f"{'因子':<14} {'IC均值':>7} {'IC_std':>7} {'ICIR':>7} {'P中位':>7} {'<0.05':>6} {'<0.10':>6} "
     f"{'Top':>8} {'Mid':>8} {'Bot':>8} {'spread':>8} {'smart':>8} {'胜率':>5} {'单调性':>8}")

# ── 表1：分位数 clip(1%,99%) ──────────────────────────
print("\n" + "=" * 150)
print(f"【表1】分位数去极值 clip(1%, 99%) — {PERIOD_INFO}")
print("=" * 150)
print(H)
print("-" * 150)

for factor in FACTORS:
    ia = ic_a.get(factor, {})
    la = layer_a.get(factor, {})
    if not ia:
        continue
    ic_dir_a = 1 if ia["ic_mean"] > 0 else -1
    mono = monotonicity(la.get("top", 0), la.get("mid", 0), la.get("bot", 0), ic_dir_a) if la else "--"
    raw_spread = la.get("spread", 0)
    adj_spread = smart_spread(ia["ic_mean"], raw_spread)
    print(f"{factor:<14} {ia['ic_mean']:>7.4f} {ia['ic_std']:>7.4f} {ia['icir']:>7.3f} "
          f"{ia.get('p_median',0):>7.3f} {ia.get('sig_5pct',0):>5.0%} {ia.get('sig_10pct',0):>5.0%} "
          f"{la.get('top',0):>7.2%} {la.get('mid',0):>7.2%} {la.get('bot',0):>7.2%} "
          f"{raw_spread:>7.2%} {adj_spread:>7.2%} {la.get('win_rate',0):>4.0%} {mono:>8}")

# ── 表2：MAD ±5σ ──────────────────────────────────────
print("\n" + "=" * 150)
print(f"【表2】MAD 去极值 ±5σ — {PERIOD_INFO}")
print("=" * 150)
print(H)
print("-" * 150)

for factor in FACTORS:
    ib = ic_b.get(factor, {})
    lb = layer_b.get(factor, {})
    if not ib:
        continue
    ic_dir_b = 1 if ib["ic_mean"] > 0 else -1
    mono = monotonicity(lb.get("top", 0), lb.get("mid", 0), lb.get("bot", 0), ic_dir_b) if lb else "--"
    raw_spread = lb.get("spread", 0)
    adj_spread = smart_spread(ib["ic_mean"], raw_spread)
    print(f"{factor:<14} {ib['ic_mean']:>7.4f} {ib['ic_std']:>7.4f} {ib['icir']:>7.3f} "
          f"{ib.get('p_median',0):>7.3f} {ib.get('sig_5pct',0):>5.0%} {ib.get('sig_10pct',0):>5.0%} "
          f"{lb.get('top',0):>7.2%} {lb.get('mid',0):>7.2%} {lb.get('bot',0):>7.2%} "
          f"{raw_spread:>7.2%} {adj_spread:>7.2%} {lb.get('win_rate',0):>4.0%} {mono:>8}")

# ── 表3：两方法对比 ────────────────────────────────────
print("\n" + "=" * 150)
print(f"【表3】两方法对比 — ICIR / smart_spread / 单调性 / 综合评级")
print("=" * 150)
print(f"{'因子':<14} {'ICIR(A)':>8} {'ICIR(B)':>8} {'ΔICIR':>8} "
      f"{'smart(A)':>10} {'smart(B)':>10} {'单调(A)':>8} {'单调(B)':>8} {'评级':>6}")
print("-" * 110)

ranked = sorted(ic_b.items(), key=lambda x: abs(x[1]["icir"]), reverse=True)
for factor, info in ranked:
    ia = ic_a.get(factor, {})
    la = layer_a.get(factor, {})
    lb = layer_b.get(factor, {})
    icir_a = ia.get("icir", 0) if ia else 0
    icir_b = info.get("icir", 0)
    delta = abs(icir_b) - abs(icir_a)   # 正=MAD更强，负=分位数更强

    smart_a = smart_spread(ia.get("ic_mean", 0), la.get("spread", 0)) if la else 0
    smart_b = smart_spread(info.get("ic_mean", 0), lb.get("spread", 0)) if lb else 0

    dir_a = 1 if ia.get("ic_mean",0) > 0 else -1 if ia else 1
    dir_b = 1 if info.get("ic_mean",0) > 0 else -1
    mono_a = monotonicity(la.get("top",0), la.get("mid",0), la.get("bot",0), dir_a) if la else "--"
    mono_b = monotonicity(lb.get("top",0), lb.get("mid",0), lb.get("bot",0), dir_b) if lb else "--"

    # 综合评级（使用 config 阈值）
    abs_icir = abs(icir_b)
    smart_pos = smart_b > 0
    a_cfg = RATING_THRESHOLDS["A"]
    b_cfg = RATING_THRESHOLDS["B"]
    c_cfg = RATING_THRESHOLDS["C"]
    if abs_icir > a_cfg["icir"] and lb.get("win_rate", 0) > a_cfg["win_rate"] and smart_pos:
        grade = "[A]"
    elif abs_icir > b_cfg["icir"] and lb.get("win_rate", 0) > b_cfg["win_rate"] and smart_pos:
        grade = "[B]"
    elif abs_icir > c_cfg["icir"]:
        grade = "[C]"
    else:
        grade = "[—]"

    print(f"{factor:<14} {icir_a:>8.3f} {icir_b:>8.3f} {delta:>+8.3f} "
          f"{smart_a:>9.2%} {smart_b:>9.2%} "
          f"{mono_a:>8} {mono_b:>8} {grade:>6}")

print(f"\n{'─' * 110}")
print(f"样本周期：{PERIOD_INFO}")
print("因子方向：IC>0 → smart=Top-Bottom（买高因子卖低因子） | IC<0 → smart=Bottom-Top（买低因子卖高因子）")
print("ΔICIR = |ICIR_MAD| - |ICIR_分位数|  正=MAD更稳健  负=分位数更宽容")
print("评级：[A]=ICIR>0.3 胜率>60% smart为正  [B]=ICIR>0.15 胜率>50% smart为正  [C]=ICIR>0.1  [—]=弱/无效/smart为负")
print("单调性：[++]严格=三层递减正确  [~~]=两端方向对但中间乱  [XX]相反=方向反转，因子失效")
print("[!] 涨跌停日不可交易，回测阶段需过滤信号。")

# ═══════════════════════════════════════════════════════
# 因子相关性矩阵（多因子合成前必查）
# ═══════════════════════════════════════════════════════

def print_corr_matrix(monthly, factors, label):
    """打印因子Pearson相关矩阵，标记高相关对"""
    t = CORR_HIGH_THRESHOLD
    print(f"\n{'─' * 110}")
    print(f"【附】因子相关性矩阵 — {label}")
    print(f"{'─' * 110}")
    print(f"|r|>{t} 的因子对几乎同义，多因子合成时只保留 ICIR 更高的那个")

    corr = monthly[factors].corr()
    n = len(factors)

    # 列头
    print(f"\n{'':>14}", end="")
    for f in factors:
        print(f"{f:>10}", end="")
    print()

    # 矩阵
    high_pairs = []
    for i, fi in enumerate(factors):
        print(f"{fi:<14}", end="")
        for j, fj in enumerate(factors):
            if i == j:
                print(f"{'—':>10}", end="")
            else:
                v = corr.loc[fi, fj]
                flag = "!" if abs(v) > CORR_HIGH_THRESHOLD else ""
                print(f"{v:>9.3f}{flag}", end="")
                if abs(v) > CORR_HIGH_THRESHOLD and i < j:
                    high_pairs.append((fi, fj, v))
        print()

    if high_pairs:
        print(f"\n[!] 发现 {len(high_pairs)} 对高相关因子（|r|>{CORR_HIGH_THRESHOLD}）：")
        for fi, fj, v in high_pairs:
            print(f"   {fi} ↔ {fj}  r={v:+.3f}")
        print("  → 多因子合成时每组只保留 ICIR 更高的那个")
    else:
        print(f"\n[OK] 无明显高相关（|r|>{CORR_HIGH_THRESHOLD}），可全部进入多因子合成。")

# 用月度数据分别算
print_corr_matrix(monthly_a, FACTORS, "分位数去极值")
print_corr_matrix(monthly_b, FACTORS, "MAD 去极值")

# ═══════════════════════════════════════════════════════
# IC 衰减曲线（MAD 数据，使用 config 中的合成因子 + 展望周期）
# ═══════════════════════════════════════════════════════
print("\n" + "=" * 110)
print(f"【附】IC 衰减曲线 — 合成因子在不同展望周期的预测力变化（MAD 数据）")
print("=" * 110)

DECAY_HORIZONS = {f"{d}日": FWD_COLS[d] for d in IC_DECAY_HORIZONS}

print(f"\n{'因子':<14}", end="")
for h in DECAY_HORIZONS:
    print(f"{h:>10}", end="")
print(f"{'峰值周期':>12}  {'衰减特征':>20}")
print("-" * 80)

for factor in SYNTHESIS_FACTORS:
    ic_by_horizon = {}
    for h_name, h_col in DECAY_HORIZONS.items():
        m = df_b.copy()
        m["period"] = m["trade_date"].dt.to_period(REBALANCE_FREQ)
        m["fwd"] = m[h_col]
        m = m.dropna(subset=["fwd"]).sort_values("trade_date")
        m = m.groupby(["period", "stock_code"]).last().reset_index()
        m = m.groupby("period").filter(lambda g: len(g) >= MIN_STOCKS_PER_PERIOD)

        ics = []
        for _, grp in m.groupby("period"):
            valid = grp[[factor, "fwd"]].dropna()
            if len(valid) < 3:
                continue
            ic, _ = spearmanr(valid[factor], valid["fwd"])
            if not np.isnan(ic):
                ics.append(ic)

        ic_by_horizon[h_name] = np.mean(ics) if ics else np.nan

    peak_h = max(ic_by_horizon, key=lambda k: abs(ic_by_horizon[k]))
    peak_v = ic_by_horizon[peak_h]
    vals = list(ic_by_horizon.values())
    max_abs = max(abs(v) for v in vals if not np.isnan(v))
    if max_abs < 0.10:
        decay = "平坦(无效)"       # 最佳周期|IC|<0.10，经济上不可交易
    elif abs(vals[0]) > abs(vals[-1]) * 1.5:
        decay = "快速衰减"
    elif abs(vals[-1]) > abs(vals[0]) * 1.5:
        decay = "滞后生效"
    else:
        decay = "缓慢衰减"

    print(f"{factor:<14}", end="")
    for h in DECAY_HORIZONS:
        v = ic_by_horizon[h]
        print(f"{'─':>10}" if np.isnan(v) else f"{v:>10.4f}", end="")
    print(f"  {peak_h}({peak_v:+.4f}){'':>4}  {decay:>20}")

print(f"\n解读：")
print("  平坦(无效) = 最佳周期|IC|<0.10，经济上不可交易")
print("  快速衰减   = 因子只有短期预测力，适合周频调仓")
print("  缓慢衰减   = 因子长期有效，月频调仓足够")
print("  滞后生效   = 因子对短期噪音大，中长期才显露")
print("  平坦       = 预测力不随周期变化")
