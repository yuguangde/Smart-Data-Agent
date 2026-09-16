# BIRD mini-dev-50：student_club 语义层

> 基于 run `40f512962d0746d8b61be69b39f06658` 中 student_club 相关 badcase 整理。
> 本文件只描述 `student_club` 数据库的语义层知识。

## 1. 业务背景

学生社团管理系统：

- `member`：社团成员
- `event`：活动
- `attendance`：活动出席记录
- `budget`：活动预算
- `expense`：支出记录
- `income`：收入记录
- `major`：专业信息
- `zip_code`：邮编信息

## 2. 表语义与字段说明

### `member` — 社团成员

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `member_id` | TEXT PK | 成员 ID |
| `first_name` | TEXT | 名 |
| `last_name` | TEXT | 姓 |
| `email` | TEXT | 邮箱 |
| `position` | TEXT | 职位 |
| `t_shirt_size` | TEXT | T 恤尺码 |
| `phone` | TEXT | 电话 |
| `zip` | INTEGER FK | 邮编 |
| `link_to_major` | TEXT FK | 专业 ID |

### `event` — 活动

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `event_id` | TEXT PK | 活动 ID | |
| `event_name` | TEXT | 活动名称 | |
| `event_date` | TEXT | 活动日期 | **ISO 时间戳格式**，例如 `2020-03-10T12:00:00` |
| `type` | TEXT | 活动类型 | 例如 `Meeting`、`Game`、`Election`、`Fundraising` |
| `notes` | TEXT | 备注 | |
| `location` | TEXT | 地点 | |
| `status` | TEXT | 状态 | `Open` / `Closed` / `Planning` |

**常见失败点：**

- `event_date` 是 ISO 时间戳，需要 `SUBSTR(event_date, 1, 10)` 提取日期
- `notes` 里可能包含重要信息，但查询时通常直接查目标字段

### `attendance` — 活动出席

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `link_to_event` | TEXT FK | 活动 ID |
| `link_to_member` | TEXT FK | 成员 ID |

### `budget` — 活动预算

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `budget_id` | TEXT PK | 预算 ID |
| `category` | TEXT | 预算类别 | 例如 `Food`、`Advertisement`、`Speaker Gifts` |
| `spent` | REAL | 已花费 |
| `remaining` | REAL | 剩余 |
| `amount` | INTEGER | 预算总额 |
| `event_status` | TEXT | 活动状态 |
| `link_to_event` | TEXT FK | 活动 ID |

### `expense` — 支出

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `expense_id` | TEXT PK | 支出 ID |
| `expense_description` | TEXT | 支出描述 |
| `expense_date` | TEXT | 支出日期 |
| `cost` | REAL | 金额 |
| `approved` | TEXT | 是否批准 | `true` / `false` |
| `link_to_member` | TEXT FK | 成员 ID |
| `link_to_budget` | TEXT FK | 预算 ID |

### `income` — 收入

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `income_id` | TEXT PK | 收入 ID | |
| `date_received` | TEXT | 收入日期 | `YYYY-MM-DD` |
| `amount` | INTEGER | 金额 | |
| `source` | TEXT | 收入来源 | 例如 `Dues`、`Fundraising` |
| `notes` | TEXT | 备注 | |
| `link_to_member` | TEXT FK | 成员 ID | |

**常见失败点：**

- 查询 fundraising 的 notes 时，数据在 `income` 表，不是 `event` 表（case 40）
- 收入/支出/预算分属不同表

### `major` — 专业

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `major_id` | TEXT PK | 专业 ID |
| `major_name` | TEXT | 专业名称 |
| `department` | TEXT | 院系 |
| `college` | TEXT | 学院 |

### `zip_code` — 邮编

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `zip_code` | INTEGER PK | 邮编 |
| `type` | TEXT | 类型 |
| `city` | TEXT | 城市 |
| `county` | TEXT | 郡/县 |
| `state` | TEXT | 州/省 |
| `short_state` | TEXT | 缩写 |

## 3. 表关系

```text
EVENT.event_id ←────── ATTENDANCE.link_to_event
MEMBER.member_id ←──── ATTENDANCE.link_to_member

EVENT.event_id ←────── BUDGET.link_to_event
BUDGET.budget_id ←──── EXPENSE.link_to_budget
MEMBER.member_id ←──── EXPENSE.link_to_member

MEMBER.member_id ←──── INCOME.link_to_member
MEMBER.link_to_major ─→ MAJOR.major_id
MEMBER.zip ───────────→ ZIP_CODE.zip_code
```

## 4. 典型正确 SQL 模式

### 模式 1：找出出席人数超过 10 人的活动名称

```sql
SELECT T1.event_name
FROM event AS T1
INNER JOIN attendance AS T2 ON T1.event_id = T2.link_to_event
GROUP BY T1.event_id
HAVING COUNT(T2.link_to_event) > 10;
```

### 模式 2：活动预算比较（Yearly Kickoff vs October Meeting）

```sql
SELECT CAST(SUM(CASE WHEN T2.event_name = 'Yearly Kickoff' THEN T1.amount ELSE 0 END) AS REAL)
     / SUM(CASE WHEN T2.event_name = 'October Meeting' THEN T1.amount ELSE 0 END)
FROM budget AS T1
INNER JOIN event AS T2 ON T1.link_to_event = T2.event_id;
```

### 模式 3：某 fundraising 的 notes

```sql
SELECT notes
FROM income
WHERE source = 'Fundraising'
  AND date_received = '2019-09-14';
```

## 5. student_club badcase 映射

| case | 问题 | 失败原因 | 正确语义 |
|---|---|---|---|
| 33 | events attended by > 10 members | 返回 `COUNT(*)` | 应返回 `event_name` |
| 37 | each expense approved? | join 条件错误导致空结果 | event→budget→expense 链 |
| 39 | total amount spent 2019 vs 2020 | join 方向错误导致 NULL | budget join event 用 `link_to_event` |
| 40 | notes of fundraising | 查 `event` 表 | 应查 `income` 表的 `notes` |
| 47 | budget in Advertisement for Yearly Kickoff vs October Meeting | 返回差值 | 应返回比值 `amount_kickoff / amount_october` |

## 6. 通用规则（student_club 重点）

### 6.1 日期处理

| 表 | 日期字段 | 格式 | 正确处理方式 |
|---|---|---|---|---|
| `event` | `event_date` | ISO 时间戳 | `SUBSTR(event_date, 1, 10)` |
| `income` | `date_received` | `YYYY-MM-DD` | 标准日期比较 |
| `expense` | `expense_date` | `YYYY-MM-DD` | 标准日期比较 |

### 6.2 Join 路径

1. `budget` 通过 `link_to_event` 关联 `event`
2. `expense` 通过 `link_to_budget` 关联 `budget`
3. `income` 通过 `link_to_member` 关联 `member`
4. `member` 通过 `link_to_major` 关联 `major`
5. `member` 通过 `zip` 关联 `zip_code`

### 6.3 常见陷阱

- **fundraising 相关查询**：`income` 表存 `source='Fundraising'`，不是 `event` 表
- **事件参与人数**：用 `attendance` 表，按 `link_to_event` 分组计数
- **budget 比较**：注意问的是 "how many times"（比值）还是 "how much more"（差值）
