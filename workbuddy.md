# workbuddy.md — WorkBuddy 开发对话上下文

> 本文件记录本项目在 **WorkBuddy** 中的开发过程，用于核验是否符合**腾讯 WorkBuddy 专项奖励**的联动条件。
>
> 说明：以下内容是本次开发会话的**结构化摘要**（按对话顺序整理的关键指令、工具调用与决策），
> 由会话内直接导出。如需完整的原始对话记录，可通过 WorkBuddy 的会话导出功能生成。

---

## 会话基本信息

| 项 | 内容 |
|---|---|
| 工具 | WorkBuddy（Agent 模式） |
| 项目工作目录 | `麦当劳MCP_github/` |
| 开发对象 | `mcd-what-to-eat`（麦当劳点餐决策 Skill） |
| 使用的 MCP 连接器 | `mcd-mcp`（麦当劳官方 MCP Server） |
| 开发语言 | Python 3（仅标准库） |

---

## 对话与执行过程

### 第 1 轮 · 确认 MCP 连接

**用户**：连接 MCP：`mcd-mcp`

**执行**：检查 `~/.workbuddy/mcp.json`，确认 `mcd-mcp` 已配置（`streamablehttp` → `https://mcp.mcd.cn`）；
实际调用 `query-my-account` 验证连通性，返回 `success: true`。

**结论**：连接器可用，无需重新配置。

---

### 第 2 轮 · 阅读参赛规则

**用户**：阅读一下这个参赛规则：`https://builderx.csdn.net/activity-site/m-code/pages`

**执行**：
- 该页面正文为**整页 WebP 图片**（4.9MB base64），HTML 内无文字；
- 抓取 HTML → 正则抽取 `data:image/webp;base64,` → 用 Pillow 转 PNG → 超长图切片 → 逐段识别；
- 页面自身声明"完整规则以麦当劳 GitHub 活动页面公示为准"，因此进一步拉取官方仓库
  `M-China-Official/mcd-developer-innovation-challenge` 的 `README.md`、`activityGuidelines.md`、
  `CONTEST_DECLARATION.md`、`RANKING.md`。

**产出**：`麦当劳程序员创意开发大赛-参赛规则速查.md`

**发现并记录的矛盾**：活动页与 `activityGuidelines.md` 均把 `mcp-config.example.json` 列为**必须**文件，
但仓库 `README.md` 的必交文件表格遗漏了它 → 按"必须"处理。

---

### 第 3 轮 · 需求确认

**用户**：按照参赛规则，我要基于 MCD-MCP 创建一个 skill，然后把这个 skill 项目上传到我连接的
github 项目上，实现功能：

1. 可以随机选择餐品，解决不知道吃什么的问题
2. 可以计算出当餐的营养信息
3. 查询附近的门店信息
4. 给出随机选择餐品的优惠券信息，可领取优惠券
5. 还有活动日历查询工具

**据此确定**：5 个功能 → 5 条工作流 → 4 个可执行脚本 + 1 个公共模块。

---

### 第 4 轮 · 摸清 MCP 工具能力

**执行**：加载并**真实调用**以下工具，确认参数规则与返回结构：

| 工具 | 验证结果 |
|---|---|
| `query-nearby-stores` | 返回门店 + `reservationTimeOptions`（含 `today` 标记与时段文本） |
| `campaign-calendar` | 返回 `### 当前时间` + `#### 日期 [状态]` 分段 + 活动条目 |
| `query-meals` | 返回 `categories[]` + `meals{}`，注意 `categories[].name` 内含换行 |
| `list-nutrition-foods` | 返回 `[160]{...}` 表格文本，字段 9 个 |
| `available-coupons` / `query-my-coupons` | 确认两种券列表格式不同，需分别解析 |

**发现的坑**（后续写入 `references/mcp-tools.md`）：
- `beType=1` 传 `beCode` 会报错，`beType=5/2/6` 必传；
- 返回是"人读说明 + JSON 原文"两层包装，说明文字里含 `data.meals.{code}.withOrder` 这类花括号示例。

---

### 第 5 轮 · 搭建项目并实现 5 项功能

**执行**：创建 `mcd-what-to-eat/`，逐个实现并**逐个实测**：

| 脚本 | 实测结果 |
|---|---|
| `mcd_common.py` | 公共解析/匹配模块；`_scan_json()` 解决花括号干扰 |
| `mcd_random_pick.py` | 连抽 8 次得到 8 个不同餐品；`--seed 1024` 两次结果一致 |
| `mcd_nutrition.py` | 巨无霸+中薯条+可乐中杯 = 949 kcal / 钠 1126 mg，与官方表核对一致 |
| `mcd_coupon_match.py` | `中薯条`→`中薯条买一送一`；`奥利奥麦旋风`→`人气麦旋风买一送一`（共同词「麦旋风」） |
| `mcd_activity_brief.py` | 从真实日历解析出 27 条活动，按今天/即将/往期分组 |

**过程中发现并修复的 3 个真实缺陷**：

1. **MCP 返回解析被花括号说明文字带偏** → 改用 `raw_decode` 逐 `{` 起点扫描；
2. **活动日历日期标题的正则用了 `\s*`**，跨行吞掉了紧随其后的"活动标题"行，
   导致 13 条未来活动全部解析失败 → 改用 `[ \t]*` 限制在同一行内；
3. **套餐名被静默匹配成同名单品**（"巨无霸三件套"→"巨无霸"），会严重低估整餐热量
   → 引入"套餐保护"：形如 `XX三件套/XX套餐` 的查询对不上就**如实报告未收录**，
   并提示先用 `query-meal-detail` 拆解。

---

### 第 6 轮 · 自检与文档

**执行**：新增 `tests/fixtures/`（仿真 MCP 返回）与 `scripts/selftest.py`，
覆盖随机性、可复现性、约束生效、营养合计、别名匹配、套餐保护、券模糊匹配、
过期券标记、日历分组等 **39 项断言**。

**结果**：`python scripts/selftest.py` → **通过 39 项，失败 0 项**。

随后补齐参赛必需文件：`README.md`、`MCP_INTEGRATION.md`、`mcp-config.example.json`、
`CONTEST_DECLARATION.md`、`references/mcp-tools.md`。

---

## 用到的 WorkBuddy 能力

| 能力 | 用途 |
|---|---|
| MCP 连接器 `mcd-mcp` | 调取菜单、营养、门店、优惠券、活动日历（**项目核心能力来源**） |
| 文件读写 | 创建全部项目文件与数据快照 |
| 命令执行 | 运行 Python 脚本、验证网络与工具链 |
| 网页抓取 | 读取 CSDN 活动页与 GitHub 官方仓库规则 |
| 多模态（图片识别） | 识别活动页的整页 WebP 图片内容 |

---

## 关键设计决策（记录了"为什么这么做"）

1. **随机用代码、不用模型**：模型采样是伪随机，会反复给出同几个热门单品 → `random.SystemRandom()`。
2. **营养用代码算、不用模型心算**：累加与供能比算错一项，整份建议就是错的。
3. **实时数据优先、快照兜底**：营养每次先调 `list-nutrition-foods`，失败才退化为仓库快照，并注明来源。
4. **宁缺毋滥**：套餐不猜、只拆 —— 宁可报"未收录"，也不拿同名单品数字顶替。
5. **不承诺券可用**：明确要求以下游 `calculate-price` / `query-store-coupons` 结果为准。
6. **默认排除周边与蘸酱**：否则"随机一餐"会抽到棒球帽。

---

## 最终产出

```
mcd-what-to-eat/
├── SKILL.md                  # Skill 定义与 5 条工作流
├── README.md
├── MCP_INTEGRATION.md
├── CONTEST_DECLARATION.md
├── mcp-config.example.json
├── workbuddy.md              # 本文件
├── references/mcp-tools.md
├── data/nutrition.json       # 158 项单品营养快照
├── scripts/                  # 5 个脚本 + 自检
└── tests/fixtures/           # 仿真 MCP 返回
```
