#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_nutrition.py — 功能②：算这一餐的营养（能量 / 蛋白质 / 脂肪 / 碳水 / 钠 / 钙）

为什么需要脚本
--------------
营养素累加、单位换算、供能比、占每日参考值百分比都是硬算术，交给人眼
或语言模型心算容易出错。这里把"查表 + 加总 + 评估"固化下来，结果可复现。

用法
----
    # 用内置快照算（data/nutrition.json）
    python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1,可乐中杯x1"

    # 用 MCP 刚刚返回的实时数据算（推荐：先调 list-nutrition-foods，把返回存成文件）
    python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1" --nutrition-file live.txt
    # 也可以直接管道
    cat live.txt | python scripts/mcd_nutrition.py "巨无霸x1" --nutrition-file -

    # 查单个餐品
    python scripts/mcd_nutrition.py --search 麦辣鸡腿汉堡

    # 看库里有啥
    python scripts/mcd_nutrition.py --list --filter 咖啡

    # 机器可读
    python scripts/mcd_nutrition.py "巨无霸x1,中薯条x1" --json

注意
----
营养库只收录**单品**。像"麦辣鸡腿汉堡三件套"这类套餐需要先用 MCP 工具
`query-meal-detail` 拆成组成单品，再交给本脚本 —— 脚本会提示未收录的条目。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_common import (  # noqa: E402
    DAILY_REFERENCE,
    NUTRIENT_LABEL,
    match_name,
    parse_qty,
    split_items,
    fmt_num,
    norm,
    load_mcp_payload,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(os.path.dirname(HERE), "data", "nutrition.json")

NUMERIC_FIELDS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]


# --------------------------------------------------------------------------
# 载入营养库
# --------------------------------------------------------------------------

def load_nutrition(path):
    """读取营养库。支持内置 JSON 快照，也支持 list-nutrition-foods 的原始返回文本。"""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    elif path == "-":
        raw = sys.stdin.read()
    elif path:
        raw = path  # 当作原始文本
    else:
        raise SystemExit("营养库文件不存在：%s" % path)

    # 优先当 JSON 快照读
    try:
        doc = json.loads(raw)
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            return doc["items"], doc.get("_source", "json snapshot")
        if isinstance(doc, list):
            return doc, "json list"
    except json.JSONDecodeError:
        pass

    # 退化为解析 MCP 文本
    from refresh_nutrition_snapshot import parse_rows, extract_text

    rows = parse_rows(extract_text(raw))
    if not rows:
        raise SystemExit("未能从给定内容中解析出营养数据")
    return rows, "list-nutrition-foods 返回"


def build_index(rows):
    by_norm = {}
    for r in rows:
        by_norm.setdefault(norm(r.get("productName")), r)
    return by_norm


# --------------------------------------------------------------------------
# 计算
# --------------------------------------------------------------------------

def compute(items_expr, rows, allow_fuzzy=True):
    index = build_index(rows)
    names = [r.get("productName") for r in rows]

    resolved, missing = [], []
    for expr in split_items(items_expr):
        name, qty = parse_qty(expr)
        # strict_combo=True：套餐名对不上就如实报"未收录"，绝不用主食的数字顶替
        hit, score, how = match_name(name, names, strict_combo=True)
        if hit is None:
            missing.append({"raw": expr, "name": name, "qty": qty, "reason": how})
            continue
        row = index.get(norm(hit))
        if row is None:
            missing.append({"raw": expr, "name": name, "qty": qty, "reason": "库中无此条目"})
            continue
        resolved.append({"raw": expr, "matched": hit, "qty": qty, "match": how, "score": score, "data": row})

    totals = {f: 0.0 for f in NUMERIC_FIELDS}
    for it in resolved:
        for f in NUMERIC_FIELDS:
            totals[f] += float(it["data"].get(f) or 0) * it["qty"]

    return resolved, missing, totals


def evaluate(totals):
    """给出简单、可解释的判断（不做医学结论）。"""
    notes = []
    kcal = totals["energyKcal"]
    sodium = totals["sodium"]
    fat = totals["fat"]
    protein = totals["protein"]
    carb = totals["carbohydrate"]

    energy_from_fat = fat * 9.0
    fat_ratio = (energy_from_fat / kcal * 100.0) if kcal > 0 else 0.0

    if kcal >= 1000:
        notes.append(("warn", "能量偏高：%.0f kcal，已接近成人每日参考值(2000 kcal)的一半以上。" % kcal))
    elif kcal >= 800:
        notes.append(("info", "能量中等偏高：%.0f kcal。" % kcal))
    elif kcal > 0 and kcal <= 500:
        notes.append(("good", "能量较为克制：%.0f kcal。" % kcal))

    if sodium >= 1500:
        notes.append(("warn", "钠很高：%.0f mg，已接近每日建议上限(2000 mg)的四分之三，建议配无糖饮品、少蘸酱。" % sodium))
    elif sodium >= 900:
        notes.append(("info", "钠偏高：%.0f mg，注意当天其余餐次清淡一些。" % sodium))

    if fat_ratio >= 40 and kcal > 0:
        notes.append(("warn", "脂肪供能比 %.0f%%，明显高于推荐的 20%%—30%%。" % fat_ratio))
    elif fat_ratio >= 32 and kcal > 0:
        notes.append(("info", "脂肪供能比 %.0f%%，略高于推荐区间。" % fat_ratio))

    if protein >= 25:
        notes.append(("good", "蛋白质充足：%.0f g，约占每日参考值的 %.0f%%。" % (protein, protein / 60 * 100)))
    elif protein >= 15:
        notes.append(("info", "蛋白质中等：%.0f g。" % protein))

    if kcal > 0 and carb * 4 / kcal < 0.30:
        notes.append(("info", "碳水供能比偏低，可能以肉/油炸类为主，可补一份玉米杯或苹果片。"))

    if not notes:
        notes.append(("info", "各项指标未触发提示阈值。"))
    return notes, {"fatEnergyRatio": round(fat_ratio, 1)}


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(resolved, missing, totals, notes, extras, rows, source):
    out = []
    out.append("## 本餐营养（麦当劳 MCP）")
    out.append("")
    out.append("数据来源：%s，共 %d 项餐品营养。" % (source, len(rows)))
    out.append("")

    if resolved:
        out.append("| 餐品 | 数量 | 能量(kcal) | 蛋白质(g) | 脂肪(g) | 碳水(g) | 钠(mg) | 钙(mg) |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for it in resolved:
            d = it["data"]
            flag = "" if it["match"] in ("精确匹配", "别名匹配（%s）" % it["raw"]) else " *"
            out.append(
                "| %s%s | %d | %s | %s | %s | %s | %s | %s |"
                % (
                    it["matched"],
                    flag,
                    it["qty"],
                    fmt_num(d.get("energyKcal")),
                    fmt_num(d.get("protein")),
                    fmt_num(d.get("fat")),
                    fmt_num(d.get("carbohydrate")),
                    fmt_num(d.get("sodium")),
                    fmt_num(d.get("calcium")),
                )
            )
        out.append(
            "| **合计** | | **%s** | **%s** | **%s** | **%s** | **%s** | **%s** |"
            % (
                fmt_num(totals["energyKcal"]),
                fmt_num(totals["protein"]),
                fmt_num(totals["fat"]),
                fmt_num(totals["carbohydrate"]),
                fmt_num(totals["sodium"]),
                fmt_num(totals["calcium"]),
            )
        )
        fuzzy = [it for it in resolved if it["match"] not in ("精确匹配",) and not it["match"].startswith("别名匹配")]
        if fuzzy:
            out.append("带 * 的条目是通过模糊/包含匹配得到的，可能不是你要的那一款，请让用户确认。")
            out.append("")

        out.append("### 占成人每日参考值")
        out.append("")
        for f in ["energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]:
            ref = DAILY_REFERENCE[f]
            val = totals[f]
            pct = val / ref * 100 if ref else 0
            bar = "█" * min(int(pct / 5), 20)
            label, unit = NUTRIENT_LABEL[f]
            out.append("- %s：%s / %s %s（%.0f%%）%s"
                       % (label, fmt_num(val), fmt_num(ref), unit, pct, bar))
        out.append("")

        out.append("### 提示")
        out.append("")
        for level, text in notes:
            mark = {"warn": "⚠️", "good": "✅", "info": "ℹ️"}.get(level, "·")
            out.append("- %s %s" % (mark, text))
        out.append("")
        out.append("脂肪供能比：%s%%" % extras["fatEnergyRatio"])
        out.append("")

    if missing:
        out.append("### ⚠️ 未能在营养库中匹配到的条目")
        out.append("")
        for m in missing:
            reason = ("（%s）" % m["reason"]) if m.get("reason") else ""
            out.append("- `%s`（解析为「%s」×%d）%s" % (m["raw"], m["name"], m["qty"], reason))
        out.append("")
        out.append(
            "营养库只收录**单品**。如果这些是套餐，请先调用 MCP 工具 `query-meal-detail` "
            "把套餐拆成组成单品，再把单品交给本脚本重算 —— 不要用同名单品的数字顶替套餐，"
            "那样会严重低估整餐热量。"
        )
        out.append("")

    out.append("> 营养素数据来自麦当劳官方公开的营养成分表；每日参考值为成人通用参考值（2000 kcal 膳食），")
    out.append("> 仅用于点餐决策辅助，不构成营养或医疗建议。")
    return "\n".join(out)


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="计算麦当劳一餐的营养素合计")
    ap.add_argument("items", nargs="?", help='餐品表达式，如 "巨无霸x1,中薯条x1,可乐中杯x1"')
    ap.add_argument("--items", dest="items_opt", help="同上（便于调用方显式传参）")
    ap.add_argument("--nutrition-file", default=None,
                    help="营养数据源：JSON 快照 / list-nutrition-foods 原始返回 / '-' 读标准输入；默认用内置快照")
    ap.add_argument("--search", help="只查某个餐品的营养")
    ap.add_argument("--list", action="store_true", help="列出营养库全部餐品")
    ap.add_argument("--filter", help="配合 --list 做关键词过滤")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    path = args.nutrition_file or DEFAULT_DB
    rows, source = load_nutrition(path)

    if args.list:
        names = sorted(r["productName"] for r in rows)
        if args.filter:
            kw = norm(args.filter)
            names = [n for n in names if kw in norm(n)]
        if args.json:
            print(json.dumps({"source": source, "count": len(names), "names": names},
                             ensure_ascii=False, indent=2))
        else:
            print("共 %d 项：\n" % len(names))
            for n in names:
                print("- " + n)
        return 0

    expr = args.search or args.items_opt or args.items
    if not expr:
        ap.error("请给出餐品表达式（或用 --search / --list）")

    resolved, missing, totals = compute(expr, rows)
    notes, extras = evaluate(totals)

    if args.json:
        print(json.dumps(
            {
                "source": source,
                "matched": [
                    {"raw": it["raw"], "matched": it["matched"], "qty": it["qty"],
                     "matchType": it["match"], "score": it["score"], "nutrition": it["data"]}
                    for it in resolved
                ],
                "missing": missing,
                "totals": totals,
                "dailyReference": DAILY_REFERENCE,
                "percentOfDaily": {f: round(totals[f] / DAILY_REFERENCE[f] * 100, 1) for f in DAILY_REFERENCE},
                "evaluation": [{"level": lv, "text": tx} for lv, tx in notes],
                "fatEnergyRatio": extras["fatEnergyRatio"],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    print(render(resolved, missing, totals, notes, extras, rows, source))
    return 0 if resolved else 2


if __name__ == "__main__":
    sys.exit(main())
