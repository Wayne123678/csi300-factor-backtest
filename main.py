"""A股量化因子回测系统 — 一键分析入口
=======================================
用法: python main.py
过程: 因子计算 → IC/分层/相关/衰减分析 → 可视化图表
"""

import subprocess, sys, os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "src")
PYTHON = sys.executable

def run(script, desc):
    path = os.path.join(SRC, script)
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    result = subprocess.run([PYTHON, path], cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"\n[FAIL] {script} 执行失败，终止。")
        sys.exit(1)

if __name__ == "__main__":
    run("factors.py",             "步骤1/3: 因子计算 (11因子 → factors表)")
    run("factor_analysis.py",     "步骤2/3: 因子分析 (IC/分层/相关矩阵/衰减曲线)")
    run("visualize.py",           "步骤3/3: 可视化 (4张图 → plots/)")

    print(f"\n{'='*60}")
    print(f"  完成！结果在 plots/ 目录下。")
    print(f"{'='*60}")
