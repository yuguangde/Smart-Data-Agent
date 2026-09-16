# BIRD mini-dev-50 Badcase 分析

> 基于 run `40f512962d0746d8b61be69b39f06658`（baseline 准确率 46%）整理。
> 本文件是评测复盘资料，**不属于语义层知识库**，用于指导语义层文档的完善。

## debit_card_specializing badcase 映射

| case | 问题 | 失败原因 | 正确语义 |
|---|---|---|---|
| 2 | In 2012, who had the least consumption in LAM? | `Currency='LAM'` | `Segment='LAM'` |
| 3 | What was the average monthly consumption of customers in SME for the year 2013? | `AVG(Consumption)` 未除 12 | `AVG(Consumption) / 12` |
| 5 | Which year recorded the most consumption of gas paid in CZK? | `strftime('%Y', Date)` 错误 | `SUBSTR(Date,1,4)` |
| 6 | What was the gas consumption peak month for SME customers in 2013? | 返回完整 `Date` | 应 `SUBSTR(Date,5,2)` |
| 7 | What is the difference in the annual average consumption... | 用 `Currency` 过滤 CZK 客户 | 应直接按 `Segment` 汇总 |
| 8 | Which of the three segments has the biggest and lowest percentage increases... | 结构复杂但本质是按 Segment 汇总 | 用 `IIF(Segment=... AND Date LIKE '2013%')` |
| 9 | How much did customer 6 consume in total between August and November 2013? | `BETWEEN '2013-08' AND '2013-11'` | `BETWEEN '201308' AND '201311'` |
| 11 | Is it true that more SMEs pay in Czech koruna than in euros? | 返回两组 count | 求差值 `SUM(CZK) - SUM(EUR)` |
| 14 | What is the highest monthly consumption in the year 2012? | `MAX(Consumption)` | 按月份分组后 `ORDER BY SUM DESC LIMIT 1` |
| 15 | Please list the product description of the products consumed in September, 2013. | 用 `transactions_1k.Date` | 应 join `yearmonth` 按 `yearmonth.Date='201309'` |
| 16 | Please list the countries of the gas stations with transactions taken place in June, 2013. | 用 `transactions_1k.Date` | 应 join `yearmonth` 按 `yearmonth.Date='201306'` |
| 17 | Among the customers who paid in euro, how many of them have a monthly consumption of over 1000? | `COUNT(DISTINCT CustomerID)` 口径 | `COUNT(*)` |
| 18 | Please list the product descriptions of the transactions taken place in the gas stations in the Czech Republic. | `'Czech Republic'` | `Country='CZE'` |
| 20 | Among the transactions made in the gas stations in the Czech Republic, how many of them are taken place after 2012/1/1? | `t.Date > '2012-01-01'` | `STRFTIME('%Y', Date) >= '2012'` |
| 21 | What kind of currency did the customer paid at 16:25:00 in 2012/8/24? | 返回重复行 | 应用 `DISTINCT` |
| 24 | What's the nationality of the customer who spent 548.4 in 2012/8/24? | 取 `customers.Currency` | 应取 `gasstations.Country` |
| 25 | What is the percentage of the customers who used EUR in 2012/8/25? | 分母用 distinct customers | 分母用行级 count |
| 26 | For the customer who paid 634.8 in 2012/8/25, what was the consumption decrease rate from Year 2012 to 2013? | 多列输出 | 单值 `(2012-2013)/2012` |
| 27 | What is the percentage of "premium" against the overall segment in Country = "SVK"? | `Segment='premium'` | `Country='SVK' AND Segment='Premium'` |
| 28 | What is the amount spent by customer "38508" at the gas stations? | `SUM(Amount)` | `SUM(Price)` |
| 29 | Who is the top spending customer...? | `SUM(Price * Amount)` | `SUM(Price / Amount)` 是单价，金额用 `SUM(Price)` |
| 30 | For all the people who paid more than 29.00 per unit of product id No.5. Give their consumption status in the August of 2012. | 输出多列 | 应取 `yearmonth.Consumption` |

## student_club badcase 映射

| case | 问题 | 失败原因 | 正确语义 |
|---|---|---|---|
| 33 | Among the events attended by more than 10 members of the Student_Club, how many of them are meetings? | 返回 `COUNT(*)` | 应返回 `event_name` |
| 37 | Was each expense in October Meeting on October 8, 2019 approved? | join 条件错误导致空结果 | event→budget→expense 链 |
| 39 | Calculate the difference of the total amount spent in all events by the Student_Club in year 2019 and 2020. | join 方向错误导致 NULL | budget join event 用 `link_to_event` |
| 40 | What was the notes of the fundraising on 2019/9/14? | 查 `event` 表 | 应查 `income` 表的 `notes` |
| 47 | How many times was the budget in Advertisement for "Yearly Kickoff" meeting more than "October Meeting"? | 返回差值 | 应返回比值 `amount_kickoff / amount_october` |

## 转化为语义层规则

以上 badcase 的共性已提炼到对应的语义层文件中：

- `docs/bird-semantic-layer-debit_card_specializing.md`
- `docs/bird-semantic-layer-student_club.md`

保留本文件只是为了追溯每个 case 的原始错误模式。
