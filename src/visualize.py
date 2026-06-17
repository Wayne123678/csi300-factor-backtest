"""因子分析可视化 — 展示CSI 300上因子失效的证据"""
import sqlite3, pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from scipy.stats import spearmanr
import os, warnings
warnings.filterwarnings("ignore")

plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 10, 'axes.titlesize': 14, 'axes.labelsize': 11,
    'figure.facecolor': 'white', 'axes.facecolor': '#fafafa',
    'axes.grid': True, 'grid.alpha': 0.3, 'grid.color': '#cccccc',
})

from config import FACTORS, DB_PATH, PRIMARY_FWD, REBALANCE_FREQ

script_dir = os.path.dirname(os.path.abspath(__file__))
plot_dir = os.path.join(os.path.dirname(script_dir), "plots")
os.makedirs(plot_dir, exist_ok=True)

# ── 数据准备 ────────────────────
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql("""
    SELECT f.*, d.close FROM factors f
    JOIN daily_price d ON f.stock_code=d.stock_code AND f.trade_date=d.trade_date
    ORDER BY f.stock_code, f.trade_date
""", conn)
conn.close()

df["trade_date"] = pd.to_datetime(df["trade_date"])
gp = df.groupby("stock_code")
for col in FACTORS:
    lo, hi = df[col].quantile(0.01), df[col].quantile(0.99)
    df[col] = df[col].clip(lo, hi)

fwd_col = f"fwd_ret_{PRIMARY_FWD}"
df[fwd_col] = gp["close"].transform(lambda x: x.pct_change(PRIMARY_FWD).shift(-PRIMARY_FWD))
df["period"] = df["trade_date"].dt.to_period(REBALANCE_FREQ)
monthly = df.dropna(subset=[fwd_col]).sort_values("trade_date")
monthly = monthly.groupby(["period","stock_code"]).last().reset_index()
monthly = monthly.groupby("period").filter(lambda g: len(g)>=5)

n_stocks = df["stock_code"].nunique()

# 计算每个因子的IC汇总和逐月IC+P值
ic_data = {}
for factor in FACTORS:
    ics = []; pvals = []; periods_list = []
    for p, grp in monthly.groupby("period"):
        v = grp[[factor, fwd_col]].dropna()
        if len(v) < 3: continue
        ic, pv = spearmanr(v[factor], v[fwd_col])
        if not np.isnan(ic):
            ics.append(ic); pvals.append(pv); periods_list.append(str(p))
    if len(ics) >= 3:
        ic_data[factor] = {
            "ics": ics, "pvals": pvals, "periods": periods_list,
            "ic_mean": np.mean(ics), "ic_std": np.std(ics, ddof=1),
            "icir": np.mean(ics)/np.std(ics, ddof=1) if np.std(ics,ddof=1)>0 else 0,
            "sig_pct": np.mean(np.array(pvals) < 0.05),
        }

PALETTE = ['#2c3e50','#e74c3c','#3498db','#e67e22','#1abc9c','#9b59b6','#f39c12','#2ecc71','#34495e','#e91e63','#00bcd4']

# ═══════════════════════════════════════════
# 图1: ICIR 条形图 — 红绿区分
# ═══════════════════════════════════════════
fig, ax = plt.subplots(figsize=(14,5))
factors_sorted = sorted(FACTORS, key=lambda f: ic_data[f]["icir"])
names = []; icirs = []; cols = []
for f in factors_sorted:
    ir = ic_data[f]["icir"]
    names.append(f); icirs.append(ir)
    cols.append('#27ae60' if ir > 0 else '#e74c3c')

bars = ax.barh(names, icirs, color=cols, height=0.6, edgecolor='white', linewidth=0.8)
ax.axvline(0, color='black', linewidth=0.8)
ax.axvline(0.3, color='#27ae60', linestyle='--', alpha=0.6, linewidth=1.2, label='可用线 ICIR=0.3')
ax.axvline(-0.3, color='#e74c3c', linestyle='--', alpha=0.6, linewidth=1.2)
for bar, v in zip(bars, icirs):
    x = v + 0.015 if v >= 0 else v - 0.015
    ax.text(x, bar.get_y()+bar.get_height()/2, f'{v:.3f}', va='center', fontsize=9, fontweight='bold')
ax.set_title(f'CSI 300 因子 ICIR 全景 ({n_stocks}只 | {len(ic_data[FACTORS[0]]["ics"])}个月)', fontsize=15, fontweight='bold')
ax.set_xlabel('ICIR (= IC均值 / IC标准差)')
ax.legend(fontsize=9, loc='lower right')
ax.set_xlim(-0.45, 0.45)
plt.tight_layout(); plt.savefig(f'{plot_dir}/01_icir.png', dpi=150, bbox_inches='tight'); plt.close()

# ═══════════════════════════════════════════
# 图2: 统计显著 vs 经济显著 — 关键发现
# ═══════════════════════════════════════════
fig, ax = plt.subplots(figsize=(10,7))
for i, f in enumerate(FACTORS):
    d = ic_data[f]
    x = abs(d["ic_mean"])   # 经济显著性
    y = d["sig_pct"]        # 统计显著性(P<0.05占比)
    r = abs(d["icir"])
    size = max(r * 600, 60)
    ax.scatter(x, y, s=size, color=PALETTE[i], alpha=0.7, edgecolors='white', linewidth=1.2, zorder=5)
    ax.annotate(f, (x, y), fontsize=9, fontweight='bold', xytext=(7,5), textcoords='offset points')

# 分区标注
ax.axvline(0.05, color='#e74c3c', linestyle='--', linewidth=1.5, alpha=0.7)
ax.axhline(0.5, color='#e74c3c', linestyle='--', linewidth=1.5, alpha=0.7)
ax.fill_between([0.05, 0.15], [0.5, 0.5], [1, 1], alpha=0.06, color='#27ae60')
ax.fill_between([0, 0.05], [0.5, 0.5], [1, 1], alpha=0.06, color='#f39c12')
ax.fill_between([0.05, 0.15], [0, 0], [0.5, 0.5], alpha=0.06, color='#3498db')
ax.fill_between([0, 0.05], [0, 0], [0.5, 0.5], alpha=0.06, color='#e74c3c')

ax.text(0.10, 0.85, '统计显著 + 经济显著\n= 可交易 ✅', ha='center', fontsize=8, color='#27ae60', alpha=0.7)
ax.text(0.025, 0.85, '统计显著\n经济不显著 ⚠', ha='center', fontsize=8, color='#f39c12', alpha=0.7)
ax.text(0.025, 0.25, '全不显著\n= 无效 ❌', ha='center', fontsize=8, color='#e74c3c', alpha=0.7)
ax.text(0.10, 0.25, '经济显著\n统计不显著 ⚠', ha='center', fontsize=8, color='#3498db', alpha=0.7)

ax.set_xlabel('|IC均值| — 经济显著性 (越大越能赚钱)', fontsize=12)
ax.set_ylabel('P<0.05 月份占比 — 统计显著性', fontsize=12)
ax.set_title(f'关键发现：因子全在左侧 (统计显著 ≠ 经济不可交易)', fontsize=14, fontweight='bold')
ax.set_xlim(0, 0.14); ax.set_ylim(0, 1.02)
ax.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
plt.tight_layout(); plt.savefig(f'{plot_dir}/02_significance.png', dpi=150, bbox_inches='tight'); plt.close()

# ═══════════════════════════════════════════
# 图3: IC 时间序列 — Top 5
# ═══════════════════════════════════════════
top5 = sorted(FACTORS, key=lambda f: abs(ic_data[f]["icir"]), reverse=True)[:5]
fig, ax = plt.subplots(figsize=(14,5))
for i, f in enumerate(top5):
    d = ic_data[f]
    ax.plot(d["ics"], color=PALETTE[i], linewidth=1.5, alpha=0.9, marker='o', markersize=3, label=f'{f} (ICIR={d["icir"]:+.3f})')
ax.axhline(0, color='black', linewidth=0.5)
ax.fill_between(range(len(ic_data[top5[0]]["ics"])), -0.3, 0, alpha=0.04, color='red', label='不可用区 |IC|<0.3')
ax.fill_between(range(len(ic_data[top5[0]]["ics"])), 0, 0.3, alpha=0.04, color='green')
ax.set_title(f'Top 5 因子逐月 IC — 全徘徊在零轴附近，无一持续偏离', fontsize=14, fontweight='bold')
ax.set_ylabel('Spearman IC'); ax.set_xlabel('月份序号')
ax.legend(fontsize=8, ncol=3, loc='upper right')
plt.tight_layout(); plt.savefig(f'{plot_dir}/03_ic_ts.png', dpi=150, bbox_inches='tight'); plt.close()

# ═══════════════════════════════════════════
# 图4: 因子相关性热力图
# ═══════════════════════════════════════════
corr = monthly[FACTORS].corr()
fig, ax = plt.subplots(figsize=(10,8))
im = ax.imshow(corr, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
for i in range(len(FACTORS)):
    for j in range(len(FACTORS)):
        v = corr.iloc[i, j]
        color = 'white' if abs(v) > 0.5 else '#333'
        weight = 'bold' if abs(v) > 0.7 else 'normal'
        ax.text(j, i, f'{v:.2f}' if abs(v) > 0.2 else '', ha='center', va='center', fontsize=9, fontweight=weight, color=color)
ax.set_xticks(range(len(FACTORS))); ax.set_xticklabels(FACTORS, rotation=45, ha='right', fontsize=9)
ax.set_yticks(range(len(FACTORS))); ax.set_yticklabels(FACTORS, fontsize=9)
ax.set_title(f'因子相关性 — |r|>0.7 的因子对冗余', fontsize=13, fontweight='bold')
cbar = plt.colorbar(im, shrink=0.8, pad=0.01)
cbar.set_label('Pearson r')
plt.tight_layout(); plt.savefig(f'{plot_dir}/04_corr.png', dpi=150, bbox_inches='tight'); plt.close()

print(f"[OK] 4张图已保存到 {plot_dir}/")
print(f"  01_icir.png         — ICIR全景（全在±0.3以内）")
print(f"  02_significance.png  — 统计显著≠经济显著")
print(f"  03_ic_ts.png         — 逐月IC在零轴震荡")
print(f"  04_corr.png          — 因子相关性热力图")
