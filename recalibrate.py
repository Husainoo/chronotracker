#!/usr/bin/env python3
"""
recalibrate.py — إعادة المعايرة الدورية (نسخة مبسطة، بلا بوابات)
==================================================================
يشغّل backtest.py (يكتب range_calibration.json) ثم sibling_backtest.py
(يكتب sibling_calibration.json) ويعتمد نتائجهما كما هي — بدون فحص أو رفض
أو استرجاع. ثم يلحق سطراً واحداً في reports/recalibration_log.txt:
التاريخ + التغطية + median APE (للقياسي وللاستعارة).

يُستدعى تلقائياً من update.command / update_nonrolex.command قبل git push
لو مرّ ≥30 يوماً على آخر سطر بالسجل. التشغيل اليدوي:
  cd ~/ChronoTracker && python3 recalibrate.py
"""
import json
import os
import subprocess
import sys
from datetime import date

os.chdir(os.path.dirname(os.path.abspath(__file__)))
py = sys.executable

subprocess.run([py, 'backtest.py'], check=True)
subprocess.run([py, 'sibling_backtest.py'], check=True)

import pandas as pd

bt = pd.read_csv('backtest_results.csv')
sib = pd.read_csv('sibling_backtest_results.csv')
sib_cal = json.load(open('sibling_calibration.json', encoding='utf-8'))

line = (f"{date.today().isoformat()} | "
        f"coverage={100 * bt['in_range'].mean():.1f}% | "
        f"median_APE={bt['ape_engine'].median():.3f} | "
        f"sibling_coverage={100 * sib_cal['coverage_in_sample']:.1f}% | "
        f"sibling_median_APE={sib['ape_sib'].median():.3f}")

os.makedirs('reports', exist_ok=True)
with open('reports/recalibration_log.txt', 'a', encoding='utf-8') as f:
    f.write(line + '\n')
print(line)
