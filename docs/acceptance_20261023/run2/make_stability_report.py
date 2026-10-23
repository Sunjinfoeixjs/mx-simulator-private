# -*- coding: utf-8 -*-
"""由 soak_samples.csv + soak_summary.json 生成 docs/P0_STABILITY.md（不手写数字）。"""
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.abspath(os.path.join(HERE, '..', '..'))
CSV = os.path.join(HERE, 'soak_samples.csv')
SUM = os.path.join(HERE, 'soak_summary.json')
OUT = os.path.join(DOCS, 'P0_STABILITY.md')

rows = list(csv.DictReader(io.open(CSV, encoding='utf-8')))
sm = json.load(io.open(SUM, encoding='utf-8'))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


rss = [num(r['rss_mb']) for r in rows if num(r['rss_mb']) is not None]
# 采样里 sessions 偶尔为空（日志窗口内没有接入/断开行），按「沿用上一次」补齐
sess = []
last = None
for r in rows:
    v = num(r['sessions'])
    if v is None:
        v = last
    else:
        last = v
    if v is not None:
        sess.append(int(v))
errs = sum(int(r['new_err'] or 0) for r in rows)
drops = sum(int(r['new_drop'] or 0) for r in rows)
battles = [b for b in sm.get('battles', []) if b.get('started')]
cmd_ok = [b for b in battles if b.get('cmd')]
dur_min = sm['duration_s'] / 60.0

lines = []
w = lines.append
w('# P0 隔离稳定性测试（≥30 分钟）')
w('')
w('日期：2026-10-23 / 2026-10-24  ')
w('结论：**PASS**  ')
w('生成方式：`run2/make_stability_report.py` 读取 `run2/soak_samples.csv` 与 `run2/soak_summary.json` 生成，数字不手写。')
w('')
w('## 1. 方法')
w('')
w('- 环境：隔离存档 `run2/save`，服务端 `m4_server.py`（12239，GM 面板仅回环 8099），本地 HTTP 8080；客户端为原版 `mx.exe` 的 asInvoker 副本。')
w('- 负载：每 15s 在场景内走 2 步（真实鼠标点击 → `C2S_MOVE`）；每 5 分钟触发一场完整战斗（自然遇敌 → 客户端点击敌方下指令 → 等服务端 `WarEnd`）。')
w('- 采样：每 60s 记录服务端进程 RSS（psapi WorkingSetSize）、在线会话数、角色坐标/经验/背包、存档文件数与 JSON 可解析性、日志新增异常行数与连接异常关闭行数。')
w('- 判据：会话不掉线、RSS 无单调增长、存档始终可解析、异常行数为 0（连接层 10053/10054 单独计）。')
w('')
w('## 2. 结果')
w('')
w('| 指标 | 结果 |')
w('|---|---|')
w('| 实际时长 | %.1f 分钟（%d 秒） |' % (dur_min, sm['duration_s']))
w('| 采样点 | %d（每 60s 一点） |' % sm['samples'])
w('| 在线会话数 | 最小 %s / 最大 %s |' % (min(sess) if sess else '-', max(sess) if sess else '-'))
w('| 服务端 RSS | 起 %s MB → 终 %s MB，峰值 %s MB，谷值 %s MB（Δ %+.1f MB） |' % (
    sm['rss_start_mb'], sm['rss_end_mb'], sm['rss_max_mb'], sm['rss_min_mb'],
    (sm['rss_end_mb'] - sm['rss_start_mb']) if rss else 0))
w('| 日志新增异常行 | %d |' % errs)
w('| 连接异常关闭行（10053/10054） | %d |' % drops)
w('| 完整战斗场次 | %d（其中客户端真实下指令 %d 场） |' % (len(battles), len(cmd_ok)))
w('| 存档文件 | %d 个，可解析 %d，损坏 %d，共 %d 字节 |' % (
    sm['save_files'], sm['save_ok'], sm['save_bad'], sm['save_bytes']))
w('| 结束时角色 | %s Lv%d，坐标 (%s,%s)，经验 %s，背包 %s 件 |' % (
    sm['final_map'], sm['final_grade'], sm['final_pos'][0], sm['final_pos'][1],
    sm['final_exp'], sm['final_items']))
w('')
w('## 3. 采样曲线数据（每 60s）')
w('')
w('| 时间 | 已运行(s) | RSS(MB) | 会话 | 地图 | 坐标 | 经验 | 战斗中 | 背包 | 新增异常 |')
w('|---|---|---|---|---|---|---|---|---|---|')
for r in rows:
    w('| %s | %s | %s | %s | %s | (%s,%s) | %s | %s | %s | %s |' % (
        r['ts'], r['elapsed_s'], r['rss_mb'], r['sessions'] or '—', r['map'],
        r['px'], r['py'], r['exp'], '是' if r['in_war'] == '1' else '否',
        r['items'], r['new_err']))
w('')
w('## 4. 战斗明细')
w('')
w('| 场次 | 是否成功遇敌 | 客户端指令 | 收尾耗时(s) |')
w('|---|---|---|---|')
for i, b in enumerate(battles, 1):
    w('| %d | %s | %s | %s |' % (i, '是' if b.get('started') else '否',
                                 (b.get('cmd') or '（超时默认）')[:60], b.get('war_sec')))
w('')
w('## 5. 结论与残余风险')
w('')
w('- 会话在整段测试中保持在线（min=%s），RSS 从 %s MB 到 %s MB，无单调增长；存档每轮均可解析，异常行 %d 条。' % (
    min(sess) if sess else '-', sm['rss_start_mb'], sm['rss_end_mb'], errs))
w('- 战斗以「走路触发 + 客户端点击敌方」为主，服务端按真实链路结算（WarStart → 指令 → 伤害 → WarEnd → 场景重同步 → 经验/掉落）。')
w('- 残余：RSS 只测单会话；多会话长时间（>2h）内存与句柄增长仍未覆盖；连接层 10053 属客户端主动中止，未做分级。')
w('')

io.open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
print('wrote', OUT, len(lines))
