---
name: mcd-what-to-eat
description: 麦当劳点餐决策助手。当用户说"不知道吃什么""随便帮我选一个""今天吃啥""帮我随机点一份""这餐多少卡路里/热量""帮我算营养""附近有哪些麦当劳门店""有什么优惠券/帮我领券""最近有什么活动"时使用。基于麦当劳 MCP（mcd-mcp）完成随机选餐、当餐营养计算、附近门店查询、优惠券匹配与领取、营销活动日历整理。
agent_created: true
---

# 麦麦吃什么 · 麦当劳点餐决策助手

一个把「选择困难」变成「三步搞定」的点餐助手：**随机决定吃什么 → 算清这餐的热量 → 找券省一笔**，
外加两个随用随查的入口：**附近门店** 和 **活动日历**。

数据全部来自**麦当劳 MCP Server**（连接器名 `mcd-mcp`）。本 Skill 不自带任何数据抓取，
只做「调 MCP → 确定性计算 → 输出结论」。

---

## 何时使用

| 用户说法 | 用哪个功能 |
|---|---|
| "不知道吃什么"、"随便来一个"、"今天吃啥"、"帮我决定"、"抽一个" | ① 随机选餐 |
| "这餐多少卡"、"热量多少"、"帮我算营养"、"蛋白质够吗"、"钠高不高" | ② 营养计算 |
| "附近有什么麦当劳"、"离我最近的门店"、"想自驾去" | ③ 附近门店 |
| "有什么券"、"这个能用券吗"、"帮我领券"、"怎么点更省" | ④ 优惠券 |
| "最近有什么活动"、"这个月有什么优惠"、"有什么可以参加的" | ⑤ 活动日历 |

---

## 前置条件

1. **MCP 连接器 `mcd-mcp` 已配置并启用**（配置见仓库 `mcp-config.example.json`）。
2. **需要门店编码（storeCode）的功能**：功能①（菜单价格）、②（套餐拆解）、④（门店可用券）
   必须先拿到 `storeCode`。获取顺序：功能③ 查门店 → 用户选定门店 → 用该门店的 `storeCode` 继续。
3. **Python 3.8+**，无第三方依赖（仅标准库）。

> ⚠️ **MCP 参数最容易踩的坑**（详见 `references/mcp-tools.md`）：
> - `beType=1`（到店自取）→ `orderType=1`，**不要传 beCode**，传了会报错；
> - `beType=5`（得来速）→ `orderType=1`，**必须传 beCode**（来自 `query-nearby-stores`）；
> - `beType=2`（麦乐送）/ `beType=6`（企业团餐）→ `orderType=2`，**必须传 beCode**（来自 `delivery-query-stores`）；
> - `query-meals` / `query-meal-detail` / `calculate-price` / `query-store-coupons` 都**必须有 storeCode**。

---

## 工作流

所有「先把 MCP 返回落盘、再交给脚本」的步骤，统一把原始返回存到临时文件，
例如 `TMPDIR/mcd/menu.json`、`TMPDIR/mcd/nutrition.json`。脚本能吃三种输入：
工具返回的**原始文本**、工具调用结果的 **JSON**、或 `-`（标准输入）。

脚本目录以下记为 `scripts/`（相对本 Skill 根目录）。

---

### ① 随机选餐 —— "不知道吃什么"

**目标**：真的随机，而不是每次都推荐巨无霸。

**步骤**

1. 确定门店：如果用户没给门店，先走功能③ 拿到 `storeCode`；用户只是随口问问，
   也可先用一个已知门店（如上次用过的）继续，但要说明。
2. 调 MCP `query-meals`（入参 `storeCode`、`orderType=1`、`beType=1`），把返回落盘为 `menu.json`。
3. 跑脚本：

   ```bash
   python scripts/mcd_random_pick.py --menu menu.json
   ```

   常用参数（对应用户的自然语言）：

   | 用户说 | 参数 |
   |---|---|
   | "30 块以内" | `--max-price 30` |
   | "想吃汉堡" | `--category 汉堡` |
   | "不要饮品/不要甜品" | `--exclude-category 饮品,甜品` |
   | "来一整套，总共 45 以内" | `--combo --budget 45` |
   | "抽两个" | `--count 2` |
   | "要正在打折的" | `--only-deals` |
   | "上次抽到的不要" | `--exclude-items "麦香鸡,巨无霸"` |

4. **把脚本输出的表格原样呈现给用户**（含 storeCode 对应的餐品 code，后续下单要用）。
5. 主动接一句："要不要顺手算一下这餐的热量 / 看看有没有券？"

> **为什么必须用脚本**：语言模型"随机"是伪随机——同一句提示词往往反复给出同几个热门单品。
> 脚本用 `random.SystemRandom()`（操作系统熵源）做真随机，并叠加预算、分类等硬约束。
> 另外脚本默认会剔除**周边商品**（棒球帽、徽章、痒痒挠…）和**蘸酱**，否则"随机一餐"很容易抽到一顶帽子。

---

### ② 当餐营养计算 —— "这餐多少卡"

**目标**：把餐品名变成可信的热量/蛋白质/脂肪/碳水/钠/钙合计。

**步骤**

1. 调 MCP `list-nutrition-foods`（无入参），把返回落盘为 `nutrition.json`。
   > 实时数据优先。若调用失败，脚本会退化为本仓库内置快照 `data/nutrition.json`（同样源自该接口）。
2. 跑脚本：

   ```bash
   python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1,可乐中杯x1" --nutrition-file nutrition.json
   ```

   其它用法：

   ```bash
   python scripts/mcd_nutrition.py --search 麦辣鸡腿汉堡        # 查单个餐品
   python scripts/mcd_nutrition.py --list --filter 咖啡        # 看库里有啥
   python scripts/mcd_nutrition.py "巨无霸x1" --json            # 机器可读
   ```

3. 输出里已经含：逐项营养、合计、占成人每日参考值百分比、以及**可解释的判断**
   （钠偏高 / 脂肪供能比超标 / 能量偏高等）。
4. **套餐怎么算**：营养库只收录**单品**。如果用户报的是套餐（如"麦辣鸡腿汉堡三件套"），
   先调 MCP `query-meal-detail`（入参 `storeCode`、`orderType`、`beType`、`code`）拿到套餐组成，
   把组成拆成单品后再交给脚本 —— 脚本也会明确提示哪些条目没匹配上。

> 口径说明必须随结果一起给出：营养素来自麦当劳公开营养表，每日参考值按 2000 kcal 膳食折算，
> **仅供点餐决策参考，不构成营养或医疗建议**。

---

### ③ 附近门店

**步骤**

1. 先调 MCP `now-time-info` 确认当前时间（用于判断"今天"和预约时段）。
2. 问清（或推断）配送方式，再调对应工具：

   | 场景 | MCP 工具 | 关键入参 |
   |---|---|---|
   | 到店自取 | `query-nearby-stores` | `beType=1`、`searchType=2`、`city`、`keyword` |
   | 得来速（自驾） | `query-nearby-stores` | `beType=5`、同上 |
   | 麦乐送（外送） | `delivery-query-stores` | 见工具说明 |
   | 收藏的门店 | `query-nearby-stores` | `searchType=1`、`beType` |

3. **呈现规则（必须遵守）**：
   - 每个门店的 `storeName` / `address` / `distance` / **`storeCode`** 都要完整展示；
     `beType=5` 时还要展示 **`beCode`**（后续工具要传）。
   - 当 `reservation=true` 时，`reservationTimeOptions` 里**每一条**都要展示，
     `today=true` 的日期标星号 `*` —— 少一条用户就没法选预约时间。
   - 收尾引导："要按哪家店接着点？"

4. 用户选定门店后，把 `storeCode`（和 `beCode`）一路带到功能①②④。

---

### ④ 优惠券匹配与领取 —— "怎么点更省"

**步骤**

1. 调 MCP `available-coupons`（可领取的券）和/或 `query-my-coupons`（我已有的券），落盘。
   - 要看**某个门店+渠道下真正能用**的券，带 `storeCode` 调 `query-store-coupons`
     （`orderType`/`beType`/`beCode` 规则同上）。
2. 把刚确定的餐品和券池一起交给脚本：

   ```bash
   python scripts/mcd_coupon_match.py --coupons coupons.json \
       --items "中薯条x1,奥利奥麦旋风x1" --store-coupons store_coupons.json
   ```

   脚本会做名称变体匹配 + 最长公共子串匹配（能认出"奥利奥麦旋风"↔"人气麦旋风买一送一"），
   标出**已过期**的券，并单列"可领取的券"。
3. 用户说"帮我领券" / "一键领" → 调 MCP `auto-bind-coupons`（无入参，自动领麦麦省全部可领券），
   领完再把新的可用券复述一遍。
4. **不要承诺"一定能用"**：`query-my-coupons` 只表示"你拥有这张券"，不校验门店/渠道/时段。
   要确认，用 `calculate-price` 把 `couponId` / `couponCode` 传进去试算，以试算结果为准。

---

### ⑤ 活动日历查询 —— "最近有什么活动"

**步骤**

1. 调 MCP `campaign-calendar`。
   - 不传参：返回当前整月的活动；
   - 传 `specifiedDate`（`yyyy-MM-dd`）：以该日为锚点，返回当天及时间线上最近的前后各一个有活动的日子（共三天）。
2. 把返回落盘后交给脚本整理成可读分栏：

   ```bash
   python scripts/mcd_activity_brief.py --calendar calendar.json --window 7
   ```

   输出会按 **🟢 今天进行中 / 🔵 即将开始 / ⚪ 往期** 分组，
   标注距今天数，并从文案里抽出**价格锚点**（如 9.9元、39.9元）和**时间区间**。
3. 用户问"哪天有活动" → 用 `now-time-info` 定当前日期，再带 `specifiedDate` 查。

---

## 组合打法（推荐）

「选择困难」的完整闭环，一次给全：

```
① 随机选餐（定 storeCode → query-meals → mcd_random_pick.py）
        ↓ 拿到餐品 code 与名称
② 营养计算（list-nutrition-foods → mcd_nutrition.py）
        ↓ 同一批餐品名
④ 优惠券  （available-coupons + query-store-coupons → mcd_coupon_match.py）
        ↓ 需要就 auto-bind-coupons 一键领取
⑤ 活动日历（campaign-calendar → mcd_activity_brief.py）补充"今天还有什么在搞"
```

输出建议固定成三段：**吃什么** → **这餐什么样** → **能省多少**。

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `query-meals` 报错 | 十有八九是 `beType=1` 却传了 `beCode`。到店自取不要传 beCode。 |
| 营养脚本提示"未匹配到" | 报的是套餐名。先用 `query-meal-detail` 拆成单品。 |
| 券匹配不到 | 券标题与餐品名不同源属正常。脚本已做模糊匹配，仍不稳妥就用 `query-store-coupons` 看门店维度。 |
| 门店列表看不到 beCode | `beType=1`（到店自取）本来就没有 beCode，只有得来速（`beType=5`）才有。 |
| 随机结果太"偏" | 加 `--category` 收窄池子，或用 `--only-deals` 只在折扣品里抽。 |
| 需要复现某次结果 | 加 `--seed <数字>`。 |

---

## 目录结构

```
mcd-what-to-eat/
├── SKILL.md                        # 本文件：Skill 定义与工作流
├── README.md                       # 面向使用者的说明
├── MCP_INTEGRATION.md              # 用到了哪些 MCP Server / Tool、调用链路与业务价值（参赛要求）
├── CONTEST_DECLARATION.md          # 参赛声明（官方原版，内容不可改动）
├── mcp-config.example.json         # 脱敏后的 MCP 配置示例（仅环境变量占位符）
├── workbuddy.md                    # 使用 WorkBuddy 开发时的对话上下文
├── references/
│   └── mcp-tools.md                # 麦当劳 MCP 工具调用速查（参数规则、返回结构）
├── data/
│   └── nutrition.json              # 营养数据快照（源自 list-nutrition-foods，兜底用）
└── scripts/
    ├── mcd_common.py               # 公共：MCP 返回解析、名称匹配、格式化
    ├── mcd_random_pick.py          # ① 随机选餐
    ├── mcd_nutrition.py            # ② 营养计算
    ├── mcd_coupon_match.py         # ④ 优惠券匹配
    ├── mcd_activity_brief.py       # ⑤ 活动日历整理
    ├── refresh_nutrition_snapshot.py  # 维护：刷新营养快照
    └── selftest.py                 # 自检：用夹具数据验证全部脚本
```

运行自检：

```bash
python scripts/selftest.py
```
