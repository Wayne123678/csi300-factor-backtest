# A股量化因子回测系统

> 2026-06-15 ~ 06-17 | 沪深300 × 11因子 | 完整量化流水线

---

## 结论速览

**CSI 300 上 11 个常见因子在 2024-2025 年全线失效。** 没有任何因子达到可用标准（|ICIR|>0.3 且 smart 为正）。

| 最佳 3 因子 | ICIR | smart | 胜率 | 评级 |
|-------------|------|-------|------|------|
| pe（低PE） | -0.27 | -0.60% | 53% | [C] |
| size（小市值） | -0.26 | +2.39% | 35% | [C] |
| pb（低PB） | -0.21 | -1.06% | 53% | [C] |

核心发现：15只精选→300只全市场，mom_20 ICIR从+0.46崩塌到-0.08。样本选择偏差、A股大盘价值风格、国家队托市三者叠加，传统价格因子在本回测期内全部失效。

诚实比造假赚钱有深度。

---

## 项目结构

```
├── data/
│   ├── stocks.db              ← daily_price / daily_valuation / stock_info / factors
│   └── csi300_stocks.json     ← 成分股
├── src/
│   ├── config.py              ← 全部参数
│   ├── build_db.py            ← 日线（腾讯OHLCV + 计算列）
│   ├── build_valuation.py     ← PE/PB历史（东方财富直连API）
│   ├── fundamentals.py        ← 基本面（东方财富直连API）
│   ├── extract_mcp.py         ← MCP兜底工具
│   ├── fill_from_mcp.py       ← MCP kline提取工具
│   ├── factors.py             ← 11因子计算
│   ├── factor_analysis.py     ← IC/分层/相关矩阵/衰减
│   └── factor_synthesis.py    ← 多因子合成（未使用）
└── logs/
    ├── 搭建流程.md
    └── 知识点问答.md
```

---

## 快速开始

```bash
# 1. 沪深300成分股
python -c "import akshare as ak,json; df=ak.index_stock_cons_csindex('000300'); ..."

# 2. 日线（298只，腾讯源）
python src/build_db.py

# 3. PE/PB历史（300只，东方财富直连）
python src/build_valuation.py

# 4. 基本面
python src/fundamentals.py

# 5. 因子计算 + 分析
python src/factors.py
python src/factor_analysis.py
```

---

## 数据管线

```
腾讯API → build_db.py → daily_price (OHLCV + 计算列)
东方财富API → build_valuation.py → daily_valuation (PE/PB日频)
东方财富API → fundamentals.py → stock_info (总股本/PE/PB)
                ↓
           factors.py (11因子)
                ↓
        factor_analysis.py (IC/分层/相关/衰减)
```

## 数据库

| 表 | 行数 | 核心内容 |
|------|------|----------|
| daily_price | 103,332 | OHLCV / volume / pct_change / turnover |
| daily_valuation | 568,520 | PE_TTM / PE动态 / PB 逐日历史 |
| stock_info | 300 | 总股本 / PE / PB 快照 |
| factors | 103,332 | 11因子逐日值 |

---

## 11因子

| 类型 | 因子 | 公式 |
|------|------|------|
| 动量 | mom_5/20/60 | close.pct_change(N) |
| 反转 | rev_1 | -pct_change(1) |
| 风险 | vol_20 | ret.rolling(20).std() |
| 活跃 | turn_5 | turnover.rolling(5).mean() |
| 活跃 | vol_ratio_5 | volume / volume.rolling(5).mean() |
| 活跃 | amp_5 | amplitude.rolling(5).mean() |
| 规模 | size | ln(close × total_shares) |
| 估值 | pe | 动态PE（日频历史） |
| 估值 | pb | 市净率（日频历史） |
