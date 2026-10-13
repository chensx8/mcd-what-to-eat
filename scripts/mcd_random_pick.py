#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_random_pick.py — 功能①：随机选餐（解决"不知道吃什么"）

为什么需要脚本
--------------
"随便帮我挑一个"这件事，语言模型做不好：它的采样是"看起来随机"，
同一句提示词往往反复给出同几个热门单品（巨无霸、麦辣鸡腿堡……）。
本脚本用操作系统熵源（random.SystemRandom）做真随机，并且可以叠加
预算、分类、折扣等硬约束，让"随机"真的随机、"约束"真的生效。

用法
----
    # 先把 MCP 查询到的菜单落盘：query-meals 的返回存成 menu.txt
    python scripts/mcd_random_pick.py --menu menu.txt

    # 只要汉堡类，30 元以内
    python scripts/mcd_random_pick.py --menu menu.txt --category 汉堡 --max-price 30

    # 排除饮品和甜品，抽 2 个单品
    python scripts/mcd_random_pick.py --menu menu.txt --exclude-category 饮品,甜品 --count 2

    # 组合模式：主食 + 小食 + 饮品，总价不超过 45 元
    python scripts/mcd_random_pick.py --menu menu.txt --combo --budget 45

    # 只从"正在打折"的商品里抽
    python scripts/mcd_random_pick.py --menu menu.txt --only-deals

    # 想复现同一次结果
    python scripts/mcd_random_pick.py --menu menu.txt --seed 1024
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_common import flatten_menu, load_mcp_payload, money, norm  # noqa: E402

# --------------------------------------------------------------------------
# 分类关键词（用于 --category / --combo 的池子划分）
# --------------------------------------------------------------------------

MAIN_KW = ["汉堡", "堡", "卷", "三件套", "四件套", "套餐", "随心配", "单人餐", "拼盘", "双人餐"]
SIDE_KW = ["薯条", "扭扭薯", "薯饼", "鸡翅", "V翅", "鸡块", "麦乐鸡", "鸡排", "脆汁鸡", "炸鸡",
           "玉米杯", "小食", "鸡薯", "脆脆薯"]
DRINK_KW = ["可乐", "雪碧", "怡泉", "咖啡", "美式", "奶铁", "雪冰", "红茶", "牛奶", "豆浆",
            "果汁", "麦炫酷", "阿芙佳朵", "桶装水", "纯悦", "饮品", "燕麦", "黑巧", "抹茶"]
DESSERT_KW = ["冰淇淋", "新地", "麦旋风", "圆筒", "甜筒", "派", "苹果片", "拉明顿"]

ROLE_KW = {"main": MAIN_KW, "side": SIDE_KW, "drink": DRINK_KW, "dessert": DESSERT_KW}
ROLE_CN = {"main": "主食", "side": "小食", "drink": "饮品", "dessert": "甜品"}

# 菜单里混着周边商品和蘸酱——这些不能当饭吃，默认排除
NONFOOD_KW = ["帽", "徽章", "丝巾", "痒痒挠", "按摩", "玩具", "周边", "联名", "摆件", "公仔",
              "卡片", "礼品卡", "蘸酱", "风味酱", "山葵酱", "礼盒"]


def haystack(item):
    return norm("%s %s %s %s" % (item.get("name", ""), item.get("category", ""),
                                 " ".join(item.get("tags") or []), item.get("code", "")))


def role_of(item):
    """判断单品属于 主食/小食/饮品/甜品 中的哪一类。"""
    h = haystack(item)
    # 套餐类优先算主食
    score = {}
    for role, kws in ROLE_KW.items():
        score[role] = sum(1 for k in kws if norm(k) in h)
    best = max(score, key=lambda r: score[r])
    return best if score[best] > 0 else "other"


def keyword_hit(item, keywords):
    h = haystack(item)
    return any(norm(k) in h for k in keywords)


def is_deal(item):
    cp, op = item.get("currentPrice"), item.get("originalPrice")
    if item.get("discountType"):
        return True
    return bool(cp is not None and op is not None and op > cp)


# --------------------------------------------------------------------------
# 筛选
# --------------------------------------------------------------------------

def apply_filters(items, args):
    pool = list(items)

    # 0) 默认剔除"不能当饭吃"的东西：周边商品、蘸酱
    if not args.include_nonfood:
        pool = [i for i in pool if not keyword_hit(i, NONFOOD_KW)]

    # 1) 单品模式下默认只要"吃的"（主食/小食），饮品甜品不进池子。
    #    用户显式给了 --category 或 --any 时不做这层限制。
    default_food_only = (not args.combo) and (not args.category) and (not args.any) and (not args.with_dessert)
    if default_food_only:
        pool = [i for i in pool if role_of(i) in ("main", "side")]

    if args.category:
        kws = [k for k in _split(args.category)]
        pool = [i for i in pool if keyword_hit(i, kws)]
    if args.exclude_category:
        kws = [k for k in _split(args.exclude_category)]
        pool = [i for i in pool if not keyword_hit(i, kws)]
    if args.exclude_items:
        bad = [norm(x) for x in _split(args.exclude_items)]
        pool = [i for i in pool if norm(i["name"]) not in bad]
    if args.only_deals:
        pool = [i for i in pool if is_deal(i)]
    if args.min_price is not None:
        pool = [i for i in pool if (i.get("currentPrice") or 0) >= args.min_price]
    if args.max_price is not None:
        pool = [i for i in pool if i.get("currentPrice") is not None and i["currentPrice"] <= args.max_price]
    return pool


def _split(s):
    import re
    return [p.strip() for p in re.split(r"[,，、;；|/]+", str(s)) if p.strip()]


# --------------------------------------------------------------------------
# 抽取
# --------------------------------------------------------------------------

def pick_single(pool, count, rng):
    if len(pool) < count:
        count = len(pool)
    if count <= 0:
        return []
    return rng.sample(pool, count)


def pick_combo(pool, budget, rng, want_dessert=False, tries=400):
    """
    组合模式：主食 + 小食 + 饮品（+ 甜品），总价落在预算内。
    会尽量让组合"像个正经的一餐"，抽不到就逐步放宽。
    """
    buckets = {"main": [], "side": [], "drink": [], "dessert": []}
    for it in pool:
        r = role_of(it)
        if r in buckets:
            buckets[r].append(it)

    def price(it):
        return it.get("currentPrice") or 0

    plans = [["main", "side", "drink"]]
    if want_dessert:
        plans = [["main", "side", "drink", "dessert"], ["main", "side", "drink"], ["main", "side"]]
    else:
        plans = [["main", "side", "drink"], ["main", "drink"], ["main", "side"]]

    for plan in plans:
        if any(not buckets[r] for r in plan):
            continue
        for _ in range(tries):
            combo = [rng.choice(buckets[r]) for r in plan]
            total = sum(price(x) for x in combo)
            if budget is None or total <= budget:
                return combo, total
        # 该方案在预算内抽不到 → 试下一种更瘦的方案
    return [], None


# --------------------------------------------------------------------------

def render(picked, pool, args, rng_desc):
    out = []
    out.append("## 🎲 随机选餐结果")
    out.append("")
    scope = "组合（主食+小食+饮品）" if args.combo else "单品"
    out.append("候选池 %d 项｜范围：%s｜抽取方式：%s。" % (len(pool), scope, rng_desc))
    if not args.category and not args.any and not args.combo:
        out.append("（默认只在主食/小食里抽，已排除饮品、甜品、周边与蘸酱；想吃别的可加 `--any` 或 `--category`）")
    out.append("")

    if not picked:
        out.append("⚠️ 在给定条件下**没有抽到**合适的组合。建议放宽预算或去掉分类限制。")
        return "\n".join(out)

    total = sum((p.get("currentPrice") or 0) for p in picked)
    out.append("| 餐品 | 编码 | 现价 | 原价 | 分类 | 标签 |")
    out.append("|---|---|---:|---:|---|---|")
    for p in picked:
        out.append(
            "| **%s** | `%s` | %s | %s | %s | %s |"
            % (
                p["name"],
                p["code"],
                money(p.get("currentPrice")),
                money(p.get("originalPrice")),
                (p.get("category") or "—").replace("\n", ""),
                "、".join(p.get("tags") or []) or "—",
            )
        )
    if len(picked) > 1 or args.combo:
        out.append("| **合计** | | **%s** | | | |" % money(total))
    out.append("")
    out.append("> 抽到的是「%s」。不满意就直接再抽一次（重新运行命令即可），"
               "随机结果每次都会变。" % "、".join(p["name"] for p in picked))
    out.append("")
    out.append("下一步建议：")
    out.append("1. 用 `mcd_nutrition.py` 算一下这一餐的热量与钠；")
    out.append("2. 用 `mcd_coupon_match.py` 看看有没有券能用在刚抽到的餐品上。")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="随机选餐（真随机 + 硬约束）")
    ap.add_argument("--menu", required=True,
                    help="菜单数据：query-meals 的返回落盘文件，或 '-' 读标准输入")
    ap.add_argument("--category", help="只要这些分类/关键词，逗号分隔，如 汉堡,鸡")
    ap.add_argument("--exclude-category", help="排除这些分类/关键词，如 饮品,甜品")
    ap.add_argument("--exclude-items", help="排除具体餐品名，逗号分隔")
    ap.add_argument("--min-price", type=float, help="最低现价（元）")
    ap.add_argument("--max-price", type=float, help="最高现价（元）")
    ap.add_argument("--budget", type=float, help="组合模式总预算（元）")
    ap.add_argument("--only-deals", action="store_true", help="只从正在打折的商品里抽")
    ap.add_argument("--count", type=int, default=1, help="抽几个单品（默认 1）")
    ap.add_argument("--combo", action="store_true", help="组合模式：主食+小食+饮品")
    ap.add_argument("--with-dessert", action="store_true", help="组合模式额外带上甜品")
    ap.add_argument("--any", action="store_true",
                    help="不做「只抽吃的」限制：饮品、甜品也进候选池")
    ap.add_argument("--include-nonfood", action="store_true",
                    help="把周边商品、蘸酱也纳入候选池（默认排除）")
    ap.add_argument("--seed", type=int, help="指定随机种子以便复现")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    payload = load_mcp_payload(args.menu)
    items = flatten_menu(payload)
    if not items:
        raise SystemExit("未能解析出菜单餐品。请确认 --menu 指向 query-meals 的返回内容。")

    pool = apply_filters(items, args)
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
    rng_desc = ("可复现种子 seed=%d" % args.seed) if args.seed is not None else "系统熵源真随机"

    if args.combo:
        picked, total = pick_combo(pool, args.budget, rng, want_dessert=args.with_dessert)
    else:
        picked = pick_single(pool, args.count, rng)
        total = sum((p.get("currentPrice") or 0) for p in picked)

    if args.json:
        print(json.dumps(
            {
                "poolSize": len(pool),
                "mode": "combo" if args.combo else "single",
                "seed": args.seed,
                "picked": picked,
                "totalPrice": round(total, 2) if picked else None,
                "filters": {
                    "category": args.category,
                    "excludeCategory": args.exclude_category,
                    "excludeItems": args.exclude_items,
                    "minPrice": args.min_price,
                    "maxPrice": args.max_price,
                    "budget": args.budget,
                    "onlyDeals": args.only_deals,
                },
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if picked else 2

    print(render(picked, pool, args, rng_desc))
    return 0 if picked else 2


if __name__ == "__main__":
    sys.exit(main())
