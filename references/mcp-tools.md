# 麦当劳 MCP 工具调用速查

> 本文件记录 `mcd-what-to-eat` 实际用到的麦当劳 MCP 工具、参数规则与返回结构。
> 内容基于对 `mcd-mcp`（`https://mcp.mcd.cn`）的实际调用验证，工具描述以麦当劳官方为准。

---

## 0. 连接信息

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer YOUR_MCP_TOKEN" }
    }
  }
}
```

Token 需在麦当劳 MCP 官网注册后申请（见 `README.md`）。**不要把真实 Token 提交到仓库**，
仓库内只保留 `mcp-config.example.json` 这类脱敏示例。

---

## 1. 四个"传递规则"参数，先把它们记住

几乎所有交易类工具都有 `beType`、`orderType`、`beCode`、`storeCode` 四个参数，规则如下：

| beType | 含义 | orderType | beCode | storeCode 来源 |
|---|---|---|---|---|
| `1` | 到店自取 | `1` | **不传**（传了会报错） | `query-nearby-stores` |
| `5` | 得来速（DT，车道取餐） | `1` | **必传** | `query-nearby-stores` |
| `2` | 麦乐送（外送） | `2` | **必传** | `delivery-query-stores` |
| `6` | 企业团餐 | `2` | **必传** | `delivery-query-stores` |

其它约定：
- `reservationDate`：**预约**场景才传，非预约不要传。格式 `yyyy-MM-dd HH:mm`。
- `query-store-coupons` 的 `storeCode` 要求**纯数字**。
- 价格字段单位是**分**，展示前要除以 100。

---

## 2. 本 Skill 用到的工具

### 2.1 菜单

#### `query-meals`
拿门店菜单。**入参**：`storeCode`(必)、`orderType`(必)、`beType`(必)、`beCode`(按上表)、`reservationDate`(预约才传)。

**返回要点**：
- `data.categories[]`：`{name, meals:[{code, tags}]}` —— 分类与餐品编码；
- `data.meals{code}`：`{name, image, currentPrice, originalPrice, discountType, canWithOrder, withOrder}` —— 餐品详情；
- `data.frequent`：常点餐品。

**坑**：`categories[].name` 里带换行（如 `"鸡肉汉堡\n/卷"`），展示或匹配前要处理。
`data.meals` 里存在 `categories` 未引用的商品（如跨分类商品），解析时两头都要覆盖。

#### `query-meal-detail`
拿**套餐组成 / 特调选项**。**入参**：`storeCode`、`orderType`、`beType`、`code`(餐品编码)、`beCode`、`reservationDate`。

**用途**：把"三件套"这类套餐拆成组成单品，才能交给营养脚本计算。
**展示规则**：`supportModify=true` 的项要标注【可特调】；不要主动展开特调列表，用户问再展示。

### 2.2 营养

#### `list-nutrition-foods`
**无入参**。返回 160 行左右的表格文本：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  ...
```

字段单位：`energyKj` 千焦 / `energyKcal` 千卡 / `protein`·`fat`·`carbohydrate` 克 / `sodium`·`calcium` 毫克。

**坑**：原始表里存在**重复行**（如 `小杯玉米杯`、`纯牛奶（盒装）`），去重后为 158 项；
只收录**单品**，套餐不在其中。

### 2.3 门店

#### `query-nearby-stores`
到店/得来速门店。**入参**：`beType`(必，1 或 5)、`searchType`(必，`1`=收藏餐厅、`2`=按位置搜)、`city`(searchType=2 必填)、`keyword`(searchType=2 必填)。

**返回要点**：`storeCode`、`storeName`、`address`、`distance`(米)、`businessStatus`、`businessStartTime/EndTime`、`reservation`、`reservationTimeOptions[]`（`date` / `today` / `reservationOptionText`）。

**展示铁律**：`storeCode` 必须完整展示；`beType=5` 时 `beCode` 也要；
`reservation=true` 时 `reservationTimeOptions` **一条都不能省**。

#### `delivery-query-stores`
麦乐送 / 团餐门店。规则同上表（`orderType=2`、必传 `beCode`）。

### 2.4 优惠券

#### `available-coupons`
**无入参**。返回"麦麦省"当前**可领取**的券，含标题、图片、状态（可领取/已领取）。

#### `query-my-coupons`
**入参**：`page`(默认1，最多5)、`pageSize`(默认200，最大200)。返回**我拥有的券**：
标题、用券价格、有效期（含可用星期与时段）、领取时间、标签（到店专用/外送专用）。

> ⚠️ 该接口**不做**门店/渠道/配送方式校验，不代表当前订单能用。

#### `query-store-coupons`
**入参**：`storeCode`(纯数字)、`orderType`、`beType`、`beCode`、`reservationDate`。
返回**指定门店+订单类型下可用**的券 —— 判断"能不能用"要看这个。

#### `auto-bind-coupons`
**无入参**。一键领取麦麦省全部当前可领券。用户说"帮我领券"时调用。

### 2.5 活动

#### `campaign-calendar`
**入参**：`specifiedDate`（可选，`yyyy-MM-dd`）。
- 不传：返回**当前整月**活动；
- 传：以该日为锚点，返回当天 + 时间线上最近的前后各一个有活动的日子（共三天）。

**返回结构**：`### 当前时间：...` + `### 活动列表：` + 多个
`#### YYYY年M月D日 [今日|往期回顾]` 分段，每段含若干
`- **活动标题**：...` + `**活动内容介绍**：...` + `**活动图片介绍**：<img ...>`。

**坑**：日期标题**同一行**可能带状态词（`今日` / `往期回顾`）；
用正则解析时**不要用 `\s*` 跨越换行**，否则会把紧随其后的"活动标题"行一并吞掉（本项目实际踩过）。

#### `now-time-info`
**无入参**。返回服务器时间戳、格式化时间、年月日、时区。用于在查询日历前确定"今天"。

---

## 3. 返回的两层包装（解析必看）

工具返回的文本通常是「**人读说明 + JSON 原文**」拼起来的，例如：

```
# API Response Information
...（字段说明，注意里面可能出现 `data.meals.{code}.withOrder` 这种含花括号的示例）...
## Original Response

{"success":true,"code":200,...,"data":{ ... }}
```

因此**不能**简单取"第一个 `{` 到最后一个 `}`"——说明文字里的 `{` 会把范围切歪。
正确做法是逐个 `{` 起点尝试 `json.JSONDecoder().raw_decode()`，取第一个解析成功
且含 `data` / `success` 的对象。本仓库 `scripts/mcd_common.py` 的 `_scan_json()` 就是这么做的。

---

## 4. 工具选择速查

| 用户意图 | 该调什么 |
|---|---|
| 有哪些餐品 / 价格多少 | `query-meals` |
| 套餐里有什么 / 能特调吗 | `query-meal-detail` |
| 热量营养 | `list-nutrition-foods` |
| 附近门店 | `query-nearby-stores`（到店/得来速）、`delivery-query-stores`（外送/团餐） |
| 有什么券可以领 | `available-coupons` |
| 我有什么券 | `query-my-coupons` |
| 这家店能用什么券 | `query-store-coupons` |
| 帮我领券 | `auto-bind-coupons` |
| 最近有什么活动 | `campaign-calendar` |
| 现在几点 / 今天是几号 | `now-time-info` |
| 这一单多少钱 | `calculate-price` |
| 下单 | `create-order` |
| 订单状态 | `query-order` / `order-list` |
| 我的积分 | `query-my-account` |
| 积分兑商品 | `mall-points-products` → `mall-product-detail` → `mall-create-order` |
