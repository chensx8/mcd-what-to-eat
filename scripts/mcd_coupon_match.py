#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_coupon_match.py — 功能④：把优惠券和"刚抽到的餐品"对上号

为什么需要脚本
--------------
麦当劳的券标题是自然语言（"中薯条买一送一""巧克力味厚松饼猪柳蛋套餐"），
餐品名又是另一套叫法（"奥利奥麦旋风""中薯条"）。靠眼睛一条条对，容易漏、
也容易错。本脚本做三件事：

1. 解析券列表（兼容 `query-my-coupons` 与 `available-coupons` 两种返回格式）；
2. 用"名称变体包含 + 相似度"给每张券和每个餐品打分，挑出真正对得上的；
3. 标记券的有效期与"可领取/已领取"状态，告诉你**先领哪几张**。

用法
----
    # 先把 MCP 返回落盘：query-my-coupons / available-coupons 的结果存成 coupons.txt
    python scripts/mcd_coupon_match.py --coupons coupons.txt --items "巨无霸x1,中薯条x1"

    # 不带餐品：只看券包里有什么、哪些还能领
    python scripts/mcd_coupon_match.py --coupons coupons.txt

    # 指定门店维度可用券（query-store-coupons 的结果）
    python scripts/mcd_coupon_match.py --coupons my_coupons.txt --store-coupons store_coupons.txt

    # 让有效期判断以某一天为准
    python scripts/mcd_coupon_match.py --coupons coupons.txt --today 2026-10-13
"""

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_common import norm, split_items, parse_qty, load_mcp_payload  # noqa: E402

# 尽量剥离"规格/状态"类词，得到餐品名的"核心词"，提高匹配率
QUALIFIERS = ["中杯", "大杯", "小杯", "盒装", "冰", "热", "中", "大", "小", "新", "经典",
              "限定", "特惠", "浓", "块", "个", "份", "杯"]

# 券标题里的营销词，不参与匹配
MARKETING = ["买一送一", "第二份半价", "免费", "优惠券", "用券价格", "专享", "特惠",
             "限时", "促销", "新品", "领取", "券"]

STATUS_AVAILABLE = ("可领取", "可领")
STATUS_OWNED = ("已领取", "已领")


# --------------------------------------------------------------------------
# 解析券
# --------------------------------------------------------------------------

DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")


def _parse_validity(text):
    """从有效期文本里抽出起止日期，返回 (start_date, end_date, raw)。"""
    if not text:
        return None, None, None
    dates = []
    for y, m, d in DATE_RE.findall(text):
        try:
            dates.append(dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    if not dates:
        return None, None, text
    return dates[0], dates[-1], text


def _benefit_from_price(price_text):
    """把 "¥13.5 (用券价格)" 这类文本变成可读的优惠描述。"""
    if not price_text:
        return None, None
    m = re.search(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", price_text)
    val = float(m.group(1)) if m else None
    if val is not None and val <= 0.01:
        return "免费", 0.0
    if val is not None:
        return "用券价 ¥%.2f" % val, val
    return price_text.strip(), None


def parse_coupons(payload, source=""):
    """
    兼容两种券列表返回：
      A) query-my-coupons 的多段式：
         ## 券标题
         - **优惠**: ¥13.5 (用券价格)
         - **有效期**: ...
         - **标签**: 到店专用、外送专用
      B) available-coupons 的条目式：
         - 优惠券标题：XXX \\
           状态：可领取 \\
    """
    if payload is None:
        return []

    # 若已经解析成结构化 JSON，直接吃
    if isinstance(payload, dict) or (isinstance(payload, list) and payload and isinstance(payload[0], dict)):
        return _from_structured(payload)

    text = payload if isinstance(payload, str) else str(payload)
    coupons = []

    # ---- 格式 A ----
    blocks = re.split(r"\n(?=##\s)", text)
    for blk in blocks:
        m = re.match(r"\s*##\s*(.+)", blk)
        if not m:
            continue
        title = m.group(1).strip()
        if not title or title.startswith("#"):
            continue
        price = _grab(blk, r"\*\*优惠\*\*\s*[:：]\s*(.+)")
        valid = _grab(blk, r"\*\*有效期\*\*\s*[:：]\s*(.+)")
        tags = _grab(blk, r"\*\*标签\*\*\s*[:：]\s*(.+)")
        got = _grab(blk, r"\*\*领取时间\*\*\s*[:：]\s*(.+)")
        benefit, value = _benefit_from_price(price)
        start, end, vraw = _parse_validity(valid)
        coupons.append({
            "title": title,
            "benefit": benefit,
            "value": value,
            "validity": vraw,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "tags": [t.strip() for t in re.split(r"[、,，]", tags or "") if t.strip()],
            "status": STATUS_OWNED[0] if got else "",
            "source": source,
        })

    if coupons:
        return coupons

    # ---- 格式 B ----
    chunks = re.split(r"\n(?=\s*[-*]\s*优惠券标题)", text)
    for blk in chunks:
        t = _grab(blk, r"优惠券标题\s*[:：]\s*([^\n\\]+)")
        if not t:
            continue
        st = _grab(blk, r"状态\s*[:：]\s*([^\n\\]+)")
        coupons.append({
            "title": t.strip(),
            "benefit": None,
            "value": None,
            "validity": None,
            "start": None,
            "end": None,
            "tags": [],
            "status": (st or "").strip(),
            "source": source,
        })

    if coupons:
        return coupons

    # ---- 兜底：整段里所有"优惠券标题"都抓一下 ----
    for t in re.findall(r"优惠券标题\s*[:：]\s*([^\n\\]+)", text):
        coupons.append({"title": t.strip(), "benefit": None, "value": None,
                        "validity": None, "start": None, "end": None,
                        "tags": [], "status": "", "source": source})
    return coupons


def _from_structured(payload):
    out = []
    rows = payload if isinstance(payload, list) else (
        payload.get("data") or payload.get("coupons") or payload.get("list") or []
    )
    if isinstance(rows, dict):
        rows = list(rows.values())
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        title = r.get("title") or r.get("couponName") or r.get("name") or r.get("couponTitle")
        if not title:
            continue
        benefit, value = _benefit_from_price(str(r.get("price") or r.get("benefit") or ""))
        start, end, vraw = _parse_validity(str(r.get("validity") or r.get("validDate") or ""))
        out.append({
            "title": title,
            "benefit": benefit or r.get("benefitText"),
            "value": value,
            "validity": vraw,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "tags": r.get("tags") or [],
            "status": str(r.get("status") or ""),
            "source": "",
        })
    return out


def _grab(text, pattern):
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------
# 匹配
# --------------------------------------------------------------------------

def name_variants(name):
    """生成餐品名的多种写法，用于提高与券标题的命中率。"""
    base = norm(name)
    variants = {base}
    if not base:
        return variants
    for q in QUALIFIERS:
        qn = norm(q)
        if qn and qn in base and len(base) > len(qn):
            variants.add(base.replace(qn, ""))
    # 去掉常见后缀
    for suf in ["套餐", "三件套", "四件套", "两件套", "单品", "汉堡"]:
        sn = norm(suf)
        if base.endswith(sn) and len(base) > len(sn) + 1:
            variants.add(base[: -len(sn)])
    return {v for v in variants if v}


def coupon_core(title):
    t = norm(title)
    for w in MARKETING:
        t = t.replace(norm(w), "")
    return t


def longest_common_substring(a, b):
    """
    最长公共子串（用于中文短名的模糊匹配）。
    "奥利奥麦旋风" vs "人气麦旋风" → "麦旋风"
    """
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best_len, best_end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best_end = i
        prev = cur
    return a[best_end - best_len : best_end]


def score_pair(item_name, coupon):
    """
    返回 (得分 0~1, 说明)。得分 >= 阈值即认为这张券可能用在餐品上。

    打分优先级：
      1) 券名（去掉营销词后）完整包含餐品名或其变体 → 强命中
      2) 最长公共子串覆盖率高 → 中命中（处理"奥利奥麦旋风"↔"人气麦旋风"这类）
      3) 整体相似度 → 弱命中
    """
    core = coupon_core(coupon["title"])
    if not core:
        return 0.0, ""
    best, why = 0.0, ""
    item_norm = norm(item_name)

    for v in name_variants(item_name):
        if not v:
            continue
        if v in core:
            coverage = len(v) / max(len(item_norm), 1)
            s = 0.7 + 0.3 * min(coverage, 1.0)
            if s > best:
                best, why = s, "券名包含「%s」" % v

    if best < 0.7:
        for v in name_variants(item_name):
            lcs = longest_common_substring(v, core)
            if len(lcs) >= 3:
                denom = min(len(v), len(core)) or 1
                ratio = len(lcs) / denom
                s = 0.5 + 0.35 * min(ratio, 1.0)
                if s > best:
                    best, why = s, "共同词「%s」" % lcs

    if best < 0.62:
        for v in name_variants(item_name):
            r = difflib.SequenceMatcher(None, v, core).ratio()
            if r > best:
                best, why = round(r * 0.85, 3), "整体相似度 %.0f%%" % (r * 100)

    return min(best, 1.0), why


def match(items, coupons, threshold=0.62, today=None):
    today = today or dt.date.today()
    matched = {}
    for raw in items:
        name, qty = parse_qty(raw)
        hits = []
        for c in coupons:
            s, why = score_pair(name, c)
            if s >= threshold:
                expired = bool(c.get("end") and dt.date.fromisoformat(c["end"]) < today)
                hits.append({"score": round(s, 3), "why": why, "expired": expired, "coupon": c})
        hits.sort(key=lambda h: (h["expired"], -h["score"]))
        matched[raw] = {"name": name, "qty": qty, "hits": hits}
    return matched


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(matched, coupons, today, store_coupons=None):
    out = []
    out.append("## 🎟️ 优惠券匹配（麦当劳 MCP）")
    out.append("")
    out.append("券池共 %d 张｜有效期判断基准日：%s" % (len(coupons), today.isoformat()))
    out.append("")

    any_hit = False
    no_hit = []
    for raw, info in matched.items():
        if not info["hits"]:
            no_hit.append(info)
            continue
        any_hit = True
        out.append("### %s（×%d）" % (info["name"], info["qty"]))
        out.append("")
        out.append("| 券 | 优惠 | 有效期 | 状态 | 匹配理由 |")
        out.append("|---|---|---|---|---|")
        for h in info["hits"]:
            c = h["coupon"]
            flag = "（⚠️ 已过期）" if h["expired"] else ""
            out.append("| %s%s | %s | %s | %s | %s |"
                       % (c["title"], flag, c.get("benefit") or "—", c.get("validity") or "—",
                          c.get("status") or "—", h["why"]))
        out.append("")

    if matched and not any_hit:
        out.append("### ⚠️ 券池里没有能直接套在所选餐品上的券")
        out.append("")
        out.append("下面列出券池里**还能领**的券，可以换个餐品，或者先领下来备用：")
        out.append("")

    if no_hit and any_hit:
        out.append("### 无可用券的餐品")
        out.append("")
        out.append("、".join("**%s**（×%d）" % (i["name"], i["qty"]) for i in no_hit))
        out.append("")

    claimable = [c for c in coupons if any(k in (c.get("status") or "") for k in STATUS_AVAILABLE)]
    if claimable:
        out.append("### 可领取的券（建议先领）")
        out.append("")
        out.append("| 券 | 优惠 | 有效期 |")
        out.append("|---|---|---|")
        for c in claimable:
            out.append("| %s | %s | %s |" % (c["title"], c.get("benefit") or "—", c.get("validity") or "—"))
        out.append("")
        out.append("> 领取动作请调用 MCP 工具 `auto-bind-coupons`（一键领取麦麦省全部可领券）。")
        out.append("")

    if store_coupons:
        out.append("### 该门店场景下可用的券")
        out.append("")
        out.append("| 券 | 优惠 | 有效期 | 状态 |")
        out.append("|---|---|---|---|")
        for c in store_coupons:
            out.append("| %s | %s | %s | %s |"
                       % (c["title"], c.get("benefit") or "—", c.get("validity") or "—",
                          c.get("status") or "—"))
        out.append("")

    out.append("> 提示：券是否真正可用，最终以 **MCP 集成/下单定价** 的结果为准——"
               "`query-my-coupons` 只表示「你拥有这张券」，不校验门店/渠道/时段。"
               "要确认可用性，请带上 `storeCode` 调 `query-store-coupons`，"
               "或用 `calculate-price` 把 `couponId/couponCode` 传进去试算。")
    return "\n".join(out)


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="把优惠券和餐品对上号")
    ap.add_argument("--coupons", required=True,
                    help="券列表数据（query-my-coupons 或 available-coupons 的返回落盘），'-' 读标准输入")
    ap.add_argument("--store-coupons", help="门店维度可用券（query-store-coupons 的返回）")
    ap.add_argument("--items", help='餐品表达式，如 "巨无霸x1,中薯条x1"')
    ap.add_argument("--today", help="有效期判断基准日 YYYY-MM-DD，默认今天")
    ap.add_argument("--threshold", type=float, default=0.62, help="匹配阈值，默认 0.62")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    coupons = parse_coupons(load_mcp_payload(args.coupons), source="my/available")
    store_coupons = parse_coupons(load_mcp_payload(args.store_coupons), source="store") \
        if args.store_coupons else None

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    items = split_items(args.items) if args.items else []
    matched = match(items, coupons, threshold=args.threshold, today=today) if items else {}

    if args.json:
        print(json.dumps({
            "today": today.isoformat(),
            "couponCount": len(coupons),
            "coupons": [{k: v for k, v in c.items() if k not in ("start",)} for c in coupons],
            "matched": {
                raw: {
                    "name": info["name"],
                    "qty": info["qty"],
                    "hits": [{"score": h["score"], "why": h["why"],
                              "expired": h["expired"], "coupon": h["coupon"]} for h in info["hits"]],
                } for raw, info in matched.items()
            },
            "claimable": [c["title"] for c in coupons
                          if any(k in (c.get("status") or "") for k in STATUS_AVAILABLE)],
            "storeCoupons": store_coupons,
        }, ensure_ascii=False, indent=2))
        return 0

    print(render(matched, coupons, today, store_coupons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
