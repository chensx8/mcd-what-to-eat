# MCP 集成说明

> 本文件说明 `mcd-what-to-eat` **实际使用**的麦当劳 MCP Server、Tool、调用流程与业务价值。
> 所有工具均来自麦当劳官方 MCP Server（`mcd-mcp`），调用参数与返回结构均经真实调用验证。

---

## 一、使用的 MCP Server

| 项 | 值 |
|---|---|
| Server 名称 | `mcd-mcp` |
| 接入方式 | Streamable HTTP |
| Endpoint | `https://mcp.mcd.cn` |
| 鉴权 | `Authorization: Bearer <MCP_TOKEN>`（麦当劳 MCP 官网申请） |
| 配置示例 | 见仓库根目录 `mcp-config.example.json` |

本项目**只使用麦当劳官方 MCP Server**，不引入其他数据源；不发请求到任何非 `.mcd.cn` 的接口。

---

## 二、使用的 Tool 清单（共 10 个）

| # | Tool | 用途 | 对应功能 |
|---|---|---|---|
| 1 | `query-meals` | 拉取指定门店的完整菜单（分类、编码、现价、原价、折扣类型、标签） | ① 随机选餐 |
| 2 | `query-meal-detail` | 查套餐组成与特调选项，用于把套餐拆成单品 | ② 营养计算 |
| 3 | `list-nutrition-foods` | 拉取餐品营养成分表（能量/蛋白/脂肪/碳水/钠/钙） | ② 营养计算 |
| 4 | `query-nearby-stores` | 查到店自取 / 得来速门店（含距离、营业时间、可预约时段） | ③ 附近门店 |
| 5 | `delivery-query-stores` | 查麦乐送 / 企业团餐门店 | ③ 附近门店 |
| 6 | `now-time-info` | 取服务器时间，用于判定"今天"与预约时段 | ③⑤ |
| 7 | `available-coupons` | 查"麦麦省"当前**可领取**的券 | ④ 优惠券 |
| 8 | `query-my-coupons` | 查用户**已拥有**的券（含用券价、有效期、适用渠道标签） | ④ 优惠券 |
| 9 | `query-store-coupons` | 查**指定门店 + 订单类型**下真正可用的券 | ④ 优惠券 |
| 10 | `auto-bind-coupons` | 一键领取麦麦省全部当前可领券 | ④ 优惠券 |

> 说明：功能④中 `calculate-price` 被列为**下游验证手段**（把 `couponId/couponCode` 传入试算），
> 本项目遵循"券是否可用以试算结果为准"的原则，在输出中明确提示，不代替用户下单。

---

## 三、调用流程

### 3.1 通用参数规则（本项目严格遵守）

麦当劳交易类工具共享 `beType` / `orderType` / `beCode` / `storeCode` 四个参数，
规则容易踩坑，本项目在 `references/mcp-tools.md` 中固化成表，并写入 `SKILL.md` 的执行约束：

| beType | 场景 | orderType | beCode |
|---|---|---|---|
| `1` | 到店自取 | `1` | **不传** |
| `5` | 得来速（DT） | `1` | **必传**（来自 `query-nearby-stores`） |
| `2` | 麦乐送 | `2` | **必传**（来自 `delivery-query-stores`） |
| `6` | 企业团餐 | `2` | **必传**（来自 `delivery-query-stores`） |

### 3.2 端到端主链路

```
【用户】"中午不知道吃什么，30 块以内，帮我把热量也算一下，顺便看看有没有券"

 ③  query-nearby-stores(beType=1, searchType=2, city, keyword)
        └─→ 拿到 storeCode（+ beType=5 时的 beCode），展示门店并让用户选定
                │
 ①  query-meals(storeCode, orderType=1, beType=1)
        └─→ 菜单 JSON 落盘
        └─→ scripts/mcd_random_pick.py --max-price 30
                └─→ 真随机抽出餐品（含 code / 名称 / 现价）
                        │
 ②  list-nutrition-foods()
        └─→ 营养表落盘
        └─→ scripts/mcd_nutrition.py "<抽到的餐品>" --nutrition-file ...
                └─→ 逐项营养 + 合计 + 占每日参考值 + 可解释判断
                     （若抽到的是套餐 → query-meal-detail 拆成单品再算）
                        │
 ④  available-coupons() + query-my-coupons()
        └─→ 券池落盘
     query-store-coupons(storeCode, orderType, beType)
        └─→ 门店维度可用券
        └─→ scripts/mcd_coupon_match.py --items "<抽到的餐品>" --coupons ... --store-coupons ...
                └─→ 匹配结果 + 可领取券清单
        └─→ 用户确认 → auto-bind-coupons()
                        │
 ⑤  campaign-calendar()
        └─→ scripts/mcd_activity_brief.py --window 7
                └─→ "今天还有什么在搞" 作为补充信息
```

### 3.3 关键工程处理

| 问题 | 处理方式 |
|---|---|
| MCP 返回是「人读说明 + JSON 原文」两层包装，说明文字里还有 `data.meals.{code}.withOrder` 这类含花括号的示例 | `mcd_common._scan_json()` 逐 `{` 起点尝试 `json.JSONDecoder().raw_decode()`，取第一个含 `data`/`success` 的对象；**不**用"第一个 `{` 到最后一个 `}`"的粗暴切片 |
| `campaign-calendar` 的日期小标题同一行带状态词（`今日`/`往期回顾`） | 正则用 `[ \t]*` 而非 `\s*`，避免跨行把紧随其后的"活动标题"行吞掉（本项目实际踩过并修复） |
| 餐品名与券标题、菜单分类名三套叫法不一致 | 名称归一化（NFKC + 去装饰符）+ 口语别名词典 + 名称变体 + 最长公共子串打分 |
| 营销文案里用数学粗体数字（`𝟗.𝟗`） | `unicodedata.normalize("NFKC", ...)` 归一化后抽取价格锚点 |

---

## 四、业务价值

| 环节 | 麦当劳侧的收益 | 用户体验侧 |
|---|---|---|
| 随机选餐 | 把菜单长尾（非头部爆款）纳入曝光，提升 SKU 触达广度；`--only-deals` 可配合促销主推品 | 不再"来回划十分钟点了老几样" |
| 营养计算 | 营养数据是官方既有资产，此前缺少**场景化出口**；本项目把它接到"点餐那一刻" | 点之前就知道这餐 949 kcal、钠 1126 mg |
| 附近门店 | 门店查询与预约时段的完整展示，直接服务于到店/得来速/外送转化 | 一句"我附近哪儿有"就有答案，且**不丢关键字段**（storeCode / beCode / 全部预约时段） |
| 优惠券 | 券的**领取率与核销率**是核心指标；本项目把券从"券包里的静态资产"变成"针对当前餐品的即时建议" | 知道"这单能用哪张券、能省多少"，并且一键领 |
| 活动日历 | 营销活动的曝光依赖用户主动翻找；本项目在点餐语境里自然带出 | "最近有什么值得吃"不用自己去翻 |

**一句话**：把「吃什么 → 多少卡 → 省多少 → 去哪儿吃 → 最近有啥」这条链路，
一次性收敛在同一个对话里。

---

## 五、合规与安全

- **不引入其他数据源**：仅调用 `mcd-mcp`，接口域名均为 `mcd.cn`。
- **不落地敏感凭证**：仓库内只有 `mcp-config.example.json`（占位符），
  真实 Token 由使用者本地配置，不入库。
- **不代替用户做交易承诺**：本项目覆盖"选 / 算 / 查 / 领券"，
  涉及下单与券可用性判断时，明确要求以下游工具（`calculate-price` / `create-order`）的实时结果为准。
- **不做医疗或营养结论**：营养输出固定附带口径说明与免责声明。
- **不采集、不存储用户数据**：所有中间数据仅落在本地临时文件，脚本不做任何回传。
