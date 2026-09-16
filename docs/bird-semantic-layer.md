# BIRD mini-dev-50 语义层总结

> 基于 run `40f512962d0746d8b61be69b39f06658`（准确率 46%，27/50 失败）的 badcase 分析整理。
> 本文档只描述语义层知识，**不修改 Agent 提示词**。

## 1. 数据集概览

| 属性 | 值 |
|---|---|
| 数据集文件 | `backend/evaluation/datasets/bird_mini_dev_50.jsonl` |
| 总 case 数 | 50 |
| 涉及数据库 | 2 个 SQLite DB |
| 通过率 | 23/50 = 46% |
| 主要失败原因 | 字段语义混淆、日期格式错误、表 join 路径错误、聚合口径偏差 |

### 1.1 涉及数据库

| DB 名称 | case 数量 | 业务场景 |
|---|---|---|
| `debit_card_specializing` | 30 | 燃料卡/加油站 B2B 客户消费 |
| `student_club` | 20 | 学生社团活动、预算、收入、支出 |

---

## 2. debit_card_specializing

### 2.1 业务背景

这是一个加油站/燃料卡 B2B 业务数据库：

- `customers`：企业客户（燃料卡持卡人）
- `gasstations`：加油站网点
- `products`：油品/商品
- `yearmonth`：客户月度消费汇总（**核心事实表**）
- `transactions_1k`：交易明细（只有 4 天抽样数据）

### 2.2 表语义与字段说明

#### `customers` — 客户主数据

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `CustomerID` | INTEGER PK | 客户唯一标识 | |
| `Segment` | TEXT | 客户细分/规模层级 | `SME` / `LAM` / `KAM` |
| `Currency` | TEXT | 客户结算货币 | `CZK`（捷克克朗） / `EUR`（欧元） |

**Segment 含义：**

- `SME` = Small and Medium Enterprises（中小企业）
- `LAM` = Large Account Management（大客户）
- `KAM` = Key Account Management（重点/关键客户）

**数据分布：**

```text
Segment  Currency  cnt     pct
SME      CZK       25134   77.43
SME      EUR        1629    5.02
LAM      CZK        3356   10.34
LAM      EUR         302    0.93
KAM      CZK        1969    6.07
KAM      EUR          71    0.22
```

**常见失败点：**

- 错误地把 `SME/LAM/KAM` 当成 `Currency` 过滤（case 2、7、8）
- 问题中提到 `LAM`，99% 是指 `Segment='LAM'`，不是 `Currency`

#### `yearmonth` — 客户月度消费汇总（核心事实表）

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `CustomerID` | INTEGER FK | 客户 ID | 关联 `customers.CustomerID` |
| `Date` | TEXT | 月份 | **纯文本格式 `YYYYMM`**，例如 `201207` |
| `Consumption` | REAL | 当月消费量 | |

**关键约束：**

- 主键：`(Date, CustomerID)`
- 数据范围：`201112` ~ `201311`（21 个月）
- **日期格式是 `YYYYMM` 文本，不是标准日期**

**常见失败点：**

- 对 `Date` 使用 `strftime('%Y', Date)` 会得到错误结果（如 `-4161`）
- 正确做法：`SUBSTR(Date, 1, 4)` 取年，`SUBSTR(Date, 5, 2)` 取月
- `BETWEEN '2013-08' AND '2013-11'` 无效，应使用 `BETWEEN '201308' AND '201311'`
- "average monthly consumption" 通常指 `AVG(Consumption) / 12`（case 3）

#### `gasstations` — 加油站网点

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `GasStationID` | INTEGER PK | 加油站唯一标识 | |
| `ChainID` | INTEGER | 连锁品牌 ID | |
| `Country` | TEXT | 国家 | `CZE` / `SVK`，不是 `Czech Republic` |
| `Segment` | TEXT | 加油站档次/定位 | `Premium` / `Discount` / `Value for money` / `Other` / `Noname` |

**常见失败点：**

- `Country` 取值是缩写：`CZE`、`SVK`，不是完整国名（case 18、20）
- `Segment` 在这里是加油站档次，和客户表的 `Segment` 含义不同
- 问题问 nationality 时，可能指加油站的 `Country`（case 24）

#### `products` — 商品/油品

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `ProductID` | INTEGER PK | 商品 ID | |
| `Description` | TEXT | 商品描述 | 捷克语/本地语言，如 `Nafta`、`Natural`、`Diesel` 等 |

#### `transactions_1k` — 交易明细（抽样）

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `TransactionID` | INTEGER PK | 交易 ID | |
| `Date` | DATE | 交易日期 | **标准日期格式 `YYYY-MM-DD`** |
| `Time` | TEXT | 交易时间 | 例如 `16:25:00` |
| `CustomerID` | INTEGER FK | 客户 ID | 关联 `customers.CustomerID` |
| `CardID` | INTEGER | 卡 ID | |
| `GasStationID` | INTEGER FK | 加油站 ID | 关联 `gasstations.GasStationID` |
| `ProductID` | INTEGER FK | 商品 ID | 关联 `products.ProductID` |
| `Amount` | INTEGER | 数量 | |
| `Price` | REAL | 总价 | **注意：Price 是总价，不是单价** |

**关键约束：**

- 只有 1000 条记录，日期范围仅 `2012-08-23` ~ `2012-08-26`
- `Price` 是总价，`单价 = Price / Amount`

**常见失败点：**

- 把 `transactions_1k.Date` 当成消费月份来统计（case 15、16、18、20）
- 月度消费统计必须使用 `yearmonth` 表
- 把 `Price` 当单价使用（case 28、29、30）
- 具体问题具体分析：
  - 消费金额/花费相关：`Price`
  - 消费量：`Consumption`（来自 `yearmonth`）

### 2.3 表关系

```text
CUSTOMERS.CustomerID  ←──────  YEARMONTH.CustomerID
CUSTOMERS.CustomerID  ←──────  TRANSACTIONS_1K.CustomerID
GASSTATIONS.GasStationID ←──  TRANSACTIONS_1K.GasStationID
PRODUCTS.ProductID ←────────  TRANSACTIONS_1K.ProductID
```

**核心 join 路径：**

1. 客户月度消费分析：`customers JOIN yearmonth ON CustomerID`
2. 交易明细分析：`transactions_1k JOIN customers / gasstations / products`

### 2.4 典型正确 SQL 模式

#### 模式 1：按 Segment 统计年度消费

```sql
SELECT T1.Segment, SUM(T2.Consumption) AS total
FROM customers AS T1
INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID
WHERE SUBSTR(T2.Date, 1, 4) = '2013'
GROUP BY T1.Segment;
```

#### 模式 2：找到某 Segment 消费最少的客户

```sql
SELECT T1.CustomerID
FROM customers AS T1
INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID
WHERE T1.Segment = 'LAM'
  AND SUBSTR(T2.Date, 1, 4) = '2012'
GROUP BY T1.CustomerID
ORDER BY SUM(T2.Consumption) ASC
LIMIT 1;
```

#### 模式 3：客户月度平均消费

```sql
-- 月均消费 = 年平均 / 12
SELECT AVG(T2.Consumption) / 12
FROM customers AS T1
INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID
WHERE SUBSTR(T2.Date, 1, 4) = '2013'
  AND T1.Segment = 'SME';
```

#### 模式 4：按国家统计加油站商品

```sql
SELECT DISTINCT T3.Description
FROM transactions_1k AS T1
INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID
INNER JOIN products AS T3 ON T1.ProductID = T3.ProductID
WHERE T2.Country = 'CZE';
```

### 2.5 debit_card_badcase 映射

| case | 问题 | 失败原因 | 正确语义 |
|---|---|---|---|
| 2 | least consumption in LAM | `Currency='LAM'` | `Segment='LAM'` |
| 3 | average monthly consumption | `AVG(Consumption)` 未除 12 | `AVG(Consumption) / 12` |
| 5 | which year most consumption | `strftime('%Y', Date)` 错误 | `SUBSTR(Date,1,4)` |
| 6 | peak month for SME | 返回完整 `Date` | 应 `SUBSTR(Date,5,2)` |
| 7 | annual avg diff between segments | 用 `Currency` 过滤 CZK 客户 | 应直接按 `Segment` 汇总 |
| 8 | biggest/lowest percentage increase | 结构复杂但本质是按 Segment 汇总 | 用 `IIF(Segment=... AND Date LIKE '2013%')` |
| 9 | customer 6 consumption Aug-Nov | `BETWEEN '2013-08' AND '2013-11'` | `BETWEEN '201308' AND '201311'` |
| 11 | more SMEs pay in CZK than EUR | 返回两组 count | 求差值 `SUM(CZK) - SUM(EUR)` |
| 14 | highest monthly consumption | `MAX(Consumption)` | 按月份分组后 `ORDER BY SUM DESC LIMIT 1` |
| 15 | products consumed in Sep 2013 | 用 `transactions_1k.Date` | 应 join `yearmonth` 按 `yearmonth.Date='201309'` |
| 16 | countries of gas stations Jun 2013 | 用 `transactions_1k.Date` | 应 join `yearmonth` 按 `yearmonth.Date='201306'` |
| 17 | customers with consumption > 1000 | `COUNT(DISTINCT CustomerID)` 口径 | `COUNT(*)` |
| 18 | products in Czech Republic | `'Czech Republic'` | `Country='CZE'` |
| 20 | transactions after 2012/1/1 | `t.Date > '2012-01-01'` | `STRFTIME('%Y', Date) >= '2012'` |
| 21 | currency paid at specific time | 返回重复行 | 应用 `DISTINCT` |
| 24 | nationality of customer | 取 `customers.Currency` | 应取 `gasstations.Country` |
| 25 | percentage of EUR customers | 分母用 distinct customers | 分母用行级 count |
| 26 | consumption decrease rate | 多列输出 | 单值 `(2012-2013)/2012` |
| 27 | premium segment percentage in SVK | `Segment='premium'` | `Country='SVK' AND Segment='Premium'` |
| 28 | amount spent by customer 38508 | `SUM(Amount)` | `SUM(Price)` |
| 29 | top spending customer | `SUM(Price * Amount)` | `SUM(Price / Amount)` 是单价，金额用 `SUM(Price)` |
| 30 | consumption status Aug 2012 | 输出多列 | 应取 `yearmonth.Consumption` |

---

## 3. student_club

### 3.1 业务背景

学生社团管理系统：

- `member`：社团成员
- `event`：活动
- `attendance`：活动出席记录
- `budget`：活动预算
- `expense`：支出记录
- `income`：收入记录
- `major`：专业信息
- `zip_code`：邮编信息

### 3.2 表语义与字段说明

#### `member` — 社团成员

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

#### `event` — 活动

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

#### `attendance` — 活动出席

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `link_to_event` | TEXT FK | 活动 ID |
| `link_to_member` | TEXT FK | 成员 ID |

#### `budget` — 活动预算

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `budget_id` | TEXT PK | 预算 ID |
| `category` | TEXT | 预算类别 | 例如 `Food`、`Advertisement`、`Speaker Gifts` |
| `spent` | REAL | 已花费 |
| `remaining` | REAL | 剩余 |
| `amount` | INTEGER | 预算总额 |
| `event_status` | TEXT | 活动状态 |
| `link_to_event` | TEXT FK | 活动 ID |

#### `expense` — 支出

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `expense_id` | TEXT PK | 支出 ID |
| `expense_description` | TEXT | 支出描述 |
| `expense_date` | TEXT | 支出日期 |
| `cost` | REAL | 金额 |
| `approved` | TEXT | 是否批准 | `true` / `false` |
| `link_to_member` | TEXT FK | 成员 ID |
| `link_to_budget` | TEXT FK | 预算 ID |

#### `income` — 收入

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

#### `major` — 专业

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `major_id` | TEXT PK | 专业 ID |
| `major_name` | TEXT | 专业名称 |
| `department` | TEXT | 院系 |
| `college` | TEXT | 学院 |

#### `zip_code` — 邮编

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `zip_code` | INTEGER PK | 邮编 |
| `type` | TEXT | 类型 |
| `city` | TEXT | 城市 |
| `county` | TEXT | 郡/县 |
| `state` | TEXT | 州/省 |
| `short_state` | TEXT | 缩写 |

### 3.3 表关系

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

### 3.4 典型正确 SQL 模式

#### 模式 1：找出出席人数超过 10 人的活动名称

```sql
SELECT T1.event_name
FROM event AS T1
INNER JOIN attendance AS T2 ON T1.event_id = T2.link_to_event
GROUP BY T1.event_id
HAVING COUNT(T2.link_to_event) > 10;
```

#### 模式 2：活动预算比较（Yearly Kickoff vs October Meeting）

```sql
SELECT CAST(SUM(CASE WHEN T2.event_name = 'Yearly Kickoff' THEN T1.amount ELSE 0 END) AS REAL)
     / SUM(CASE WHEN T2.event_name = 'October Meeting' THEN T1.amount ELSE 0 END)
FROM budget AS T1
INNER JOIN event AS T2 ON T1.link_to_event = T2.event_id;
```

#### 模式 3：某 fundraising 的 notes

```sql
SELECT notes
FROM income
WHERE source = 'Fundraising'
  AND date_received = '2019-09-14';
```

### 3.5 student_club badcase 映射

| case | 问题 | 失败原因 | 正确语义 |
|---|---|---|---|
| 33 | events attended by > 10 members | 返回 `COUNT(*)` | 应返回 `event_name` |
| 37 | each expense approved? | join 条件错误导致空结果 | event→budget→expense 链 |
| 39 | total amount spent 2019 vs 2020 | join 方向错误导致 NULL | budget join event 用 `link_to_event` |
| 40 | notes of fundraising | 查 `event` 表 | 应查 `income` 表的 `notes` |
| 47 | budget in Advertisement for Yearly Kickoff vs October Meeting | 返回差值 | 应返回比值 `amount_kickoff / amount_october` |

---

## 4. 跨数据库通用规则

### 4.1 日期处理

| DB | 表 | 日期字段 | 格式 | 正确处理方式 |
|---|---|---|---|---|
| debit_card | yearmonth | `Date` | `YYYYMM` 文本 | `SUBSTR(Date, 1, 4)` / `SUBSTR(Date, 5, 2)` |
| debit_card | transactions_1k | `Date` | `YYYY-MM-DD` | 标准日期比较 |
| student_club | event | `event_date` | ISO 时间戳 | `SUBSTR(event_date, 1, 10)` |
| student_club | income | `date_received` | `YYYY-MM-DD` | 标准日期比较 |
| student_club | expense | `expense_date` | `YYYY-MM-DD` | 标准日期比较 |

### 4.2 聚合口径

1. **"average monthly consumption"** 通常表示月平均值。对于 debit_card 的 `yearmonth` 数据，年内的月均 = `AVG(Consumption) / 12`。
2. **"how many more A than B"** 通常要返回单一差值，而不是两列/两行。
3. **"percentage of X"** 注意分母定义：是总行数、distinct 客户数、还是特定子集。
4. **"top spending customer"** 金额口径要统一，debit_card 里用 `SUM(Price)`，不是 `SUM(Amount)` 也不是 `SUM(Price*Amount)`。

### 4.3 Join 最佳实践

1. debit_card 的月度Consumption 分析：**必须**用 `yearmonth` 表，不要试图用 `transactions_1k`。
2. debit_card 的交易商品/加油站分析：用 `transactions_1k` join `gasstations`/`products`。
3. student_club：`budget` 通过 `link_to_event` 关联 `event`；`expense` 通过 `link_to_budget` 关联 `budget`。

### 4.4 字段值规范化

- 国家代码：`CZE` / `SVK`（不是 `Czech Republic` / `Slovakia`）
- 布尔值：`approved` 字段是 `true` / `false` 文本
- Segment 值大小写：`SME`/`LAM`/`KAM` 大写；gasstations.Segment 首字母大写 `Premium`

---
  