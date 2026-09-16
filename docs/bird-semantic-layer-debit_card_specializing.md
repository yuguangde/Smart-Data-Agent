# BIRD mini-dev-50：debit_card_specializing 语义层

> 基于 run `40f512962d0746d8b61be69b39f06658` 中 debit_card 相关 badcase 整理。
> 本文件只描述 `debit_card_specializing` 数据库的语义层知识。

## 1. 业务背景

这是一个加油站/燃料卡 B2B 业务数据库：

- `customers`：企业客户（燃料卡持卡人）
- `gasstations`：加油站网点
- `products`：油品/商品
- `yearmonth`：客户月度消费汇总（**核心事实表**）
- `transactions_1k`：交易明细（只有 4 天抽样数据）

## 2. 表语义与字段说明

### `customers` — 客户主数据

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

### `yearmonth` — 客户月度消费汇总（核心事实表）

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

### `gasstations` — 加油站网点

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

### `products` — 商品/油品

| 字段 | 类型 | 业务含义 | 备注 |
|---|---|---|---|
| `ProductID` | INTEGER PK | 商品 ID | |
| `Description` | TEXT | 商品描述 | 捷克语/本地语言，如 `Nafta`、`Natural`、`Diesel` 等 |

### `transactions_1k` — 交易明细（抽样）

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

## 3. 表关系

```text
CUSTOMERS.CustomerID  ←──────  YEARMONTH.CustomerID
CUSTOMERS.CustomerID  ←──────  TRANSACTIONS_1K.CustomerID
GASSTATIONS.GasStationID ←──  TRANSACTIONS_1K.GasStationID
PRODUCTS.ProductID ←────────  TRANSACTIONS_1K.ProductID
```

**核心 join 路径：**

1. 客户月度消费分析：`customers JOIN yearmonth ON CustomerID`
2. 交易明细分析：`transactions_1k JOIN customers / gasstations / products`

## 4. 典型正确 SQL 模式

### 模式 1：按 Segment 统计年度消费

```sql
SELECT T1.Segment, SUM(T2.Consumption) AS total
FROM customers AS T1
INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID
WHERE SUBSTR(T2.Date, 1, 4) = '2013'
GROUP BY T1.Segment;
```

### 模式 2：找到某 Segment 消费最少的客户

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

### 模式 3：客户月度平均消费

```sql
-- 月均消费 = 年平均 / 12
SELECT AVG(T2.Consumption) / 12
FROM customers AS T1
INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID
WHERE SUBSTR(T2.Date, 1, 4) = '2013'
  AND T1.Segment = 'SME';
```

### 模式 4：按国家统计加油站商品

```sql
SELECT DISTINCT T3.Description
FROM transactions_1k AS T1
INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID
INNER JOIN products AS T3 ON T1.ProductID = T3.ProductID
WHERE T2.Country = 'CZE';
```

## 5. debit_card badcase 映射

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

## 6. 通用规则（debit_card 重点）

### 6.1 日期处理

| 表 | 日期字段 | 格式 | 正确处理方式 |
|---|---|---|---|
| `yearmonth` | `Date` | `YYYYMM` 文本 | `SUBSTR(Date, 1, 4)` / `SUBSTR(Date, 5, 2)` |
| `transactions_1k` | `Date` | `YYYY-MM-DD` | 标准日期比较 |

- **monthly consumption 统计必须使用 `yearmonth` 表**，不要用 `transactions_1k.Date`。
- 对 `yearmonth.Date` 禁用 `strftime`。

### 6.2 聚合口径

- "average monthly consumption" = `AVG(Consumption) / 12`
- "how many more A than B" = 单一差值，不是两行
- "percentage of X customers" = 注意分母是行级计数还是 distinct 计数
- "top spending customer" 金额用 `SUM(Price)`，不是 `SUM(Amount)` 也不是 `SUM(Price*Amount)`

### 6.3 字段值规范化

- 国家代码：`CZE` / `SVK`（不是 `Czech Republic` / `Slovakia`）
- 客户 Segment：`SME`/`LAM`/`KAM`（大写）
- 加油站 Segment：`Premium` / `Discount` / `Value for money` / `Other` / `Noname`（首字母大写）
