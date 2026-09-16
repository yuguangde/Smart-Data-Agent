# BIRD mini-dev-50 语义层总结

> 基于 run `40f512962d0746d8b61be69b39f06658`（准确率 46%，27/50 失败）的 badcase 分析整理。

## 数据集概览

| 属性 | 值 |
|---|---|
| 数据集文件 | `backend/evaluation/datasets/bird_mini_dev_50.jsonl` |
| 总 case 数 | 50 |
| 涉及数据库 | 2 个 SQLite DB |
| 原始通过率 | 23/50 = 46% |
| 接入检索后 | 32/50 = 64% |
| 主要失败原因 | 字段语义混淆、日期格式错误、表 join 路径错误、聚合口径偏差 |

### 涉及数据库

| DB 名称 | case 数量 | 业务场景 |
|---|---|---|
| `debit_card_specializing` | 30 | 燃料卡/加油站 B2B 客户消费 |
| `student_club` | 20 | 学生社团活动、预算、收入、支出 |

---

## 数据库专属语义层

为避免不同数据库的语义知识互相干扰，已将语义层拆分为两个独立文件：

- **[bird-semantic-layer-debit_card_specializing.md](./bird-semantic-layer-debit_card_specializing.md)** — `debit_card_specializing` 数据库
- **[bird-semantic-layer-student_club.md](./bird-semantic-layer-student_club.md)** — `student_club` 数据库

评测时会根据 `db_path` 自动选择对应的数据库语义文件进行检索。
