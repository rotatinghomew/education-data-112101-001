# 直播教育消费投诉的关联研判与处置进度

本地资料用于理解直播教育消费投诉的关联研判所涉及的记录关系。文件均为合成数据，标识符不对应真实个人、机构或账号。

## 目录

- `data/`：UTF-8 编码、首行为字段名的逗号分隔资料。空字段表示资料未提供，不代表数值为零或业务状态已否定。
- `complaint_analysis/`：可在项目内持续使用的投诉分析能力（仅依赖 Python 标准库）。
- `tests/`：资料结构检查与分析规则测试。

日期使用 ISO 8601 写法；时间字段为空时表示该记录没有提供时间。金额和计数字段以文本保存，具体解释须结合字段名及记录上下文。

## 运行与检查

需要 Python 3.11 或更高版本，无第三方运行依赖。

```bash
# 运行全部测试（兼容 python3 -I 隔离模式）
python3 -I -m unittest discover -s tests

# 资料校验
python3 -I complaint_analysis/cli.py --data data validate

# 关联线索（机器建议，含人工研判状态与依据）
python3 -I complaint_analysis/cli.py --data data clues

# 案件台账（仅由工作人员决定形成）
python3 -I complaint_analysis/cli.py --data data cases

# 按时间/课程/经营主体的汇总，每个结果可追溯
python3 -I complaint_analysis/cli.py --data data metrics

# 血缘追溯：单条投诉、案件或统计单元格
python3 -I complaint_analysis/cli.py --data data trace --complaint C-101
python3 -I complaint_analysis/cli.py --data data trace --case CASE-MERGE-e30a8a4d17
python3 -I complaint_analysis/cli.py --data data trace --metric merchant:M-3
```

所有命令支持 `--json`（机器可读输出）、`--out 文件`（落盘），以及
`--same-topic-days`、`--cross-course-days`、`--session-minutes` 三个研判时间窗参数。
也可以作为库调用：`from complaint_analysis import run; report, dataset = run("data")`。

## 数据文件

- `data/complaints.csv`：投诉记录。可选列 `session_ref` 把投诉挂接到直播场次。
- `data/orders.csv`：订单与退款状态（none/partial/full）。
- `data/case_decisions.csv`：工作人员处置决定（见下）。
- `data/evidence.csv`：证据记录，`supersedes` 列指向被本条补正的旧证据。
- `data/sessions.csv`（可选）：直播场次资料，缺失时场次类线索自动跳过。

## 分析模型与边界

### 1. 机器只出线索，工作人员才定案件

- **线索（clue）** 由稳定标识计算产生，绝不按名称相似性合并，避免把不同机构误并：
  - `same_household_topic`：同一当事人在时间窗内就同一经营主体+课程的多次投诉；
    “补充说明”类记录挂接为补充成员，不单独成案、不重复计数；
  - `cross_course_sales`：同一当事人在时间窗内跨课程/经营主体的多起投诉，识别跨课程销售链；
  - `shared_session`：多条投诉指向同一直播场次（显式挂接或按时间容差推断）；
  - `shared_order`：多条非补充投诉匹配到同一订单（收款主体线索）；
  - `recurring_script`：不同当事人生效证据中的销售话术文本一致、时间接近。
- 每条线索携带规则名、匹配键、时间/证据依据和人工决定依据，状态为
  `candidate`（待研判）、`confirmed`（人工确认）、`excluded`（人工排除）、`retracted`（更正后撤回）。
- **案件（case）只由 `case_decisions.csv` 中的人工决定产生**；没有人工决定覆盖的投诉
  只在线索中以 candidate 存在，不进入案件汇总，机器关联不会被直接当成事实。

### 2. 更正可重算，判断可保留

- 证据 `supersedes` 形成补正链：旧证据失效但记录保留，血缘中可查到替代证据；
- `household_correction` 证据修正当事人归属，后续关联按更正后的归属计算；
- 更正使旧分组解散或缩小时，旧线索以 `retracted` 状态保留并注明更正证据，
  已有的 `keep_separate` / `merge_review` 等人工判断及其 reason_code 不删除，
  已确认案件继续保留在台账中；
- 整个流水线是纯函数：同一输入与配置必然得到同一输出（测试覆盖可重复性），
  输入更正后重新运行即得到一致更新，报告附带输入指纹便于对账。

### 3. 处置动作与案件状态

人工动作：`open_case`（转个案）、`merge_review`（确认合并）、`split`（拆分）、
`keep_separate`（确认不合并）、`needs_documents`（待补资料）。

案件状态由投诉状态与订单退款推导，且依据可追溯：

- `awaiting_documents`：存在待补资料决定且仍有未撤回投诉；
- `closed_withdrawn`：非补充投诉全部撤回；
- `resolved_refunded`：全部可计入成员对应订单均为全额退款；
- `active`：其余仍需处理的情形（部分退款、缺订单等会在依据中注明）。

### 4. 汇总口径与可追溯性

- 可计入独立投诉 = 非撤回（`status != withdrawn`）且非补充类；
- 撤回数、补充数、全额退款数单独列示，跨课程销售与退款状态不改变原始提交记录；
- 汇总按时间（月）、课程、经营主体三个维度输出，并附 `overall` 总体对账行；
- 每个统计单元格携带 `complaint_ids`、`eligible_complaint_ids`、`case_ids`、
  `decision_ids` 等完整血缘，可用 `trace --metric 维度:键` 下钻到投诉、案件与处置依据，
  而不是一张孤立的汇总表。

## 模块结构

| 模块 | 职责 |
| --- | --- |
| `loading.py` | CSV 读取、空字段语义、结构与引用完整性校验 |
| `evidence.py` | 证据补正链、销售话术生效值、当事人更正归属 |
| `matching.py` | 投诉与订单的稳定标识+时间邻近匹配（线索层与案件层共用口径） |
| `linkage.py` | 五类关联线索生成、人工研判状态、更正撤回留痕 |
| `cases.py` | 依据人工决定生成案件、补充挂接、案件状态推导 |
| `metrics.py` | 三维度汇总与每单元格血缘 |
| `lineage.py` | 投诉/案件/统计结果的追溯 |
| `pipeline.py` | 纯函数式编排、输入指纹、结果交叉校验 |
| `cli.py` | 命令行入口 |
