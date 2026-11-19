# <任务ID> <任务名称> — 任务报告

- 执行者：Codex　日期：YYYY-MM-DD　分支/提交：`codex/Px` @ `<commit hash>`
- 固件：`<文件名>`（SHA-256：`...`）　CanMV 版本：`...`　配置：`reports/data/<ID>/config.json`

## 1. 做了什么
（简述实现内容、关键设计选择及理由；偏离规范之处及对应 CR）

## 2. 验收标准逐条对照
| # | 验收标准（照抄任务书） | 结果 | 数值 / 证据链接 |
|---|---|---|---|
| 1 | | PASS / FAIL / 无法测 | |

## 3. 复现方法
```bash
# 电脑端测试
pytest -q
# 板上运行
python tools/board/board.py run k230/exp/xxx.py --log reports/data/<ID>/run.log
# 指标计算
python tools/analysis/xxx.py reports/data/<ID>/
```

## 4. 人工步骤记录
（哪些步骤由队员完成、如何完成、可能引入的误差；未执行的写“未执行”）

## 5. 异常、不确定性与未覆盖项

## 6. 对方案的质疑与建议
（发现的设计缺陷、假设不成立之处；已提交的 CR 编号）
