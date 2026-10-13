# 麦当劳今天吃啥？ · mcd-what-to-eat

> 一个基于**麦当劳 MCP**的点餐决策 Skill：**随机决定吃什么 → 算清这餐的热量 → 找券省一笔**，
> 外加两个随用随查的入口：**附近门店** 和 **活动日历**。

「今天吃什么」是每天都要重复一次的决策疲劳。打开外卖软件来回划十分钟，
最后还是点了老几样——这是很多人真实的午饭体验。本项目就是冲着这个问题去的：
**把选择交给真正的随机，把判断交给确定性的计算。**

本项目为 **1024 程序员节 · 麦当劳程序员创意开发大赛** 参赛作品，基于麦当劳官方 MCP Server 开发。

---

## 五大能力

| # | 能力 | 解决什么 | 用到的 MCP 工具 |
|---|---|---|---|
| ① | **随机选餐** | "不知道吃什么"——按预算/分类/折扣等硬约束真随机抽取 | `query-meals` |
| ② | **当餐营养计算** | "这餐多少卡"——热量、蛋白质、脂肪、碳水、钠、钙合计与占比 | `list-nutrition-foods`、`query-meal-detail` |
| ③ | **附近门店查询** | "哪儿有麦当劳"——到店自取 / 得来速 / 麦乐送门店与预约时段 | `query-nearby-stores`、`delivery-query-stores`、`now-time-info` |
| ④ | **优惠券匹配与领取** | "怎么点更省"——把券和餐品对上号，一键领取可领的券 | `available-coupons`、`query-my-coupons`、`query-store-coupons`、`auto-bind-coupons` |
| ⑤ | **活动日历查询** | "最近有什么活动"——把营销日历整理成看得懂的分栏 | `campaign-calendar`、`now-time-info` |

---

## 为什么不是"纯提示词的 Skill"

如果只是写一段提示词让模型自由发挥，会踩三个坑，所以本项目把这三件事**固化成了代码**：

1. **随机不能交给语言模型**。模型的采样是"看起来随机"：同一句"随便帮我选一个"，
   它往往反复给出同几个热门单品。`mcd_random_pick.py` 用 `random.SystemRandom()`
   （操作系统熵源）做真随机。
   *实测：连续 12 次抽取，结果分布在不同餐品上；同时支持 `--seed` 精确复现。*

2. **营养加总不能靠心算**。热量/蛋白质/脂肪/碳水/钠/钙的累加、单位换算、供能比计算，
   一旦错一项，整份"健康建议"就是错的。脚本把这些算死。
   *实测：巨无霸+中薯条+可乐中杯 = 949 kcal / 钠 1126 mg，与官方营养表逐项核对一致。*

3. **名称对不上是常态**。券叫"人气麦旋风买一送一"，餐品叫"奥利奥麦旋风"；
   菜单叫"鸡肉汉堡\n/卷"，餐品叫"麦辣鸡腿汉堡"。脚本用**名称变体 + 最长公共子串**
   做匹配，并给每个匹配结果附上"为什么匹配上了"。
   *实测：`奥利奥麦旋风` ↔ `人气麦旋风买一送一` 通过共同词「麦旋风」命中。*

另外还有一处**主动的"宁缺毋滥"**：营养库只收录单品，所以当用户报的是"巨无霸三件套"时，
脚本**明确报告"未收录、请先拆解套餐"**，而不是拿"巨无霸"的数字顶替 ——
后者会把整餐热量严重低估。

---

## 快速开始

### 1. 申请麦当劳 MCP Token

1. 打开麦当劳 MCP 官网，右上角【登录】，用手机号验证登录；
2. 点【控制台】→【激活】，申请 MCP Token；
3. 阅读并同意服务协议后，一键复制 Token。

> 📖 官方指引：https://github.com/M-China/mcd-mcp-server

### 2. 在 WorkBuddy 中配置连接器

左侧边栏【专家·技能·连接器】→【连接器】→ 右上角【自定义连接器】→【配置 MCP】，
填入（**记得把 `YOUR_MCP_TOKEN` 换成真实 Token**）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

保存后回到【自定义连接器】，把 `mcd-mcp` **启用**。

### 3. 装上本 Skill

把本仓库放到 WorkBuddy 的 skill 目录（用户级 `~/.workbuddy/skills/mcd-what-to-eat/`，
或项目级 `<项目>/.workbuddy/skills/mcd-what-to-eat/`），重启会话即可被自动识别。

### 4. 直接说话

```
我中午不知道吃什么，帮我挑一个，30 块以内
这餐多少卡路里？
附近有哪些麦当劳？
我有什么券能用在刚点的东西上？帮我领一下
最近有什么活动？
```

---

## 直接跑脚本（不依赖对话）

脚本只有标准库依赖，Python 3.8+ 即可。用 `-` 可以直接吃 MCP 返回的标准输入。

```bash
# ① 随机选餐（先把 query-meals 的返回存成 menu.json）
python scripts/mcd_random_pick.py --menu menu.json --max-price 30
python scripts/mcd_random_pick.py --menu menu.json --combo --budget 45
python scripts/mcd_random_pick.py --menu menu.json --only-deals --seed 1024

# ② 营养计算
python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1,可乐中杯x1"
python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1" --nutrition-file nutrition.json
python scripts/mcd_nutrition.py --search 麦辣鸡腿汉堡

# ④ 优惠券匹配
python scripts/mcd_coupon_match.py --coupons coupons.json --items "中薯条x1,奥利奥麦旋风x1"

# ⑤ 活动日历
python scripts/mcd_activity_brief.py --calendar calendar.json --window 7
```

### 自检

```bash
python scripts/selftest.py
```

用 `tests/fixtures/` 里的仿真 MCP 返回，把四个脚本跑一遍（39 项断言），
**不需要连 MCP、不需要 Token** 就能验证项目是否可用。

---

## 目录结构

```
mcd-what-to-eat/
├── SKILL.md                            # Skill 定义与工作流（给 Agent 看的）
├── README.md                           # 本文件
├── MCP_INTEGRATION.md                  # MCP 集成说明：工具、调用链路、业务价值
├── CONTEST_DECLARATION.md              # 参赛声明（官方原版）
├── mcp-config.example.json             # 脱敏后的 MCP 配置示例
├── workbuddy.md                        # 使用 WorkBuddy 开发时的对话上下文
├── references/
│   └── mcp-tools.md                    # 麦当劳 MCP 工具调用速查
├── data/
│   └── nutrition.json                  # 营养数据快照（158 项单品，源自 list-nutrition-foods）
├── scripts/
│   ├── mcd_common.py                   # 公共：MCP 返回解析、名称匹配、格式化
│   ├── mcd_random_pick.py              # ① 随机选餐
│   ├── mcd_nutrition.py                # ② 营养计算
│   ├── mcd_coupon_match.py             # ④ 优惠券匹配
│   ├── mcd_activity_brief.py           # ⑤ 活动日历整理
│   ├── refresh_nutrition_snapshot.py   # 维护：从 MCP 返回刷新营养快照
│   └── selftest.py                     # 自检
└── tests/
    └── fixtures/                       # 仿真 MCP 返回（供自检使用）
```

---

## 设计上的几个取舍

- **实时数据优先，快照兜底**。营养查表每次都会先调 `list-nutrition-foods`；
  只有调用失败时才退化为仓库内的快照，并在输出里注明数据来源。
- **套餐不猜、只拆**。营养库不含套餐；遇到套餐一律提示用 `query-meal-detail` 拆解，
  避免"看起来算出来了，其实少算一半"。
- **券不承诺可用**。`query-my-coupons` 只说明"你拥有这张券"，不校验门店/渠道/时段。
  要确认可用性得看 `query-store-coupons`，或用 `calculate-price` 试算 —— 输出里写明了这一点。
- **周边和蘸酱默认排除**。菜单里混着棒球帽、徽章、痒痒挠和蘸酱，
  "随机一餐"抽到一顶帽子就很尴尬，所以默认只从能吃的东西里抽。

---

## 依赖

- Python 3.8+（**只用标准库**，无第三方依赖）
- 麦当劳 MCP 连接器 `mcd-mcp`（需自行申请 Token）
- 推荐在 WorkBuddy 中运行（Skill 的完整形态），脚本也可独立执行

---

## 免责声明

- 本项目是**参赛作品**，由参赛者独立开发，**非麦当劳官方产品**。
- 项目输出仅供参考，不构成医疗、营养或其他专业建议。
- 餐品信息、价格、库存、优惠与门店状态均以**麦当劳官方渠道的实时结果**为准。
- 营养素数据来自麦当劳公开的营养成分表；每日参考值为成人通用参考值（按 2000 kcal 膳食折算）。

## 许可

[MIT](LICENSE)
