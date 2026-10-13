#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_activity_brief.py — 功能⑤：把营销活动日历整理成"能看的一页"

为什么需要脚本
--------------
`campaign-calendar` 返回的是按日期堆叠的长文本，动辄上万字，混着往期回顾、
今日活动、未来预告，还夹带大量 emoji 和宣传语。人（和语言模型）直接读，
很容易漏掉"哪个活动今天最后一天"。

本脚本做三件事：
1. 解析日历文本 → 结构化 (日期, 标题, 正文, 状态)；
2. 按"今天 / 即将开始 / 往期"分组，算出距今几天；
3. 抽取正文里的价格与日期区间（含 𝟗.𝟗 这类数学粗体数字），给出关键卖点。

用法
----
    # 先把 campaign-calendar 的返回落盘
    python scripts/mcd_activity_brief.py --calendar calendar.txt

    # 只看未来 7 天
    python scripts/mcd_activity_brief.py --calendar calendar.txt --window 7

    # 指定基准日
    python scripts/mcd_activity_brief.py --calendar calendar.txt --today 2026-10-13

    # 机器可读
    python scripts/mcd_activity_brief.py --calendar calendar.txt --json
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_common import load_mcp_payload  # noqa: E402

DATE_HEADER_RE = re.compile(
    # 注意：这里不能用 \s*（会跨行吞掉紧随其后的"活动标题"行），
    # 必须用 [ \t]* 把匹配限制在同一行内。
    r"^#{2,6}[ \t]*(\d{4})[ \t]*年[ \t]*(\d{1,2})[ \t]*月[ \t]*(\d{1,2})[ \t]*日[ \t]*(.*)$",
    re.M,
)
NOW_RE = re.compile(r"当前时间[:：]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
TITLE_RE = re.compile(r"\*\*\s*活动标题\s*\*\*\s*[:：]\s*(.+)")
CONTENT_RE = re.compile(r"\*\*\s*活动内容介绍\s*\*\*\s*[:：]\s*([\s\S]*?)(?=\n\s*(?:\*\*|#{2,6}\s|<img)|\Z)")
PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
RANGE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:至|-|~|—)\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

STATUS_TODAY = ("今日", "今天")
STATUS_PAST = ("往期", "往期回顾")


def nfkc(s):
    return unicodedata.normalize("NFKC", str(s or ""))


def clean(s):
    """去掉 markdown 强调符、多余空白，保留可读文本。"""
    s = nfkc(s)
    s = s.replace("**", "").replace("\\", "")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def parse_calendar(payload):
    """把日历文本解析成 [{date, status, title, content, prices, ranges}]。"""
    if payload is None:
        return [], None
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    text = payload

    now = None
    m = NOW_RE.search(text)
    if m:
        try:
            now = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            now = None

    # 找所有日期小标题的位置，按它们切段
    marks = list(DATE_HEADER_RE.finditer(text))
    items = []
    for idx, mk in enumerate(marks):
        start = mk.end()
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        body = text[start:end]
        try:
            d = dt.date(int(mk.group(1)), int(mk.group(2)), int(mk.group(3)))
        except ValueError:
            continue
        status_raw = clean(mk.group(4))

        # 段内可能有多个活动
        titles = list(TITLE_RE.finditer(body))
        if not titles:
            continue
        for i, t in enumerate(titles):
            seg_end = titles[i + 1].start() if i + 1 < len(titles) else len(body)
            seg = body[t.end() : seg_end]
            cm = CONTENT_RE.search(seg)
            content = clean(cm.group(1)) if cm else clean(seg)
            title = clean(t.group(1))
            if not title:
                continue
            prices = sorted({float(p) for p in PRICE_RE.findall(nfkc(content))})
            ranges = ["%s月%s日-%s月%s日" % (a, b, c, d2)
                      for a, b, c, d2 in RANGE_RE.findall(nfkc(content))]
            items.append({
                "date": d.isoformat(),
                "dateRaw": "%d年%d月%d日" % (d.year, d.month, d.day),
                "statusRaw": status_raw,
                "title": title,
                "content": content,
                "prices": prices,
                "ranges": ranges,
            })
    return items, now


def classify(items, today):
    for it in items:
        d = dt.date.fromisoformat(it["date"])
        delta = (d - today).days
        it["delta"] = delta
        if delta < 0:
            it["group"] = "past"
        elif delta == 0:
            it["group"] = "today"
        else:
            it["group"] = "upcoming"
    return items


def summarize(item, limit=160):
    txt = re.sub(r"\s+", " ", item["content"])
    bits = []
    if item["prices"]:
        bits.append("价格锚点：" + "、".join("%g元" % p for p in item["prices"][:4]))
    if item["ranges"]:
        bits.append("时间区间：" + "、".join(item["ranges"][:2]))
    if len(txt) > limit:
        txt = txt[:limit] + "…"
    return txt, bits


def render(items, today, window=None):
    out = []
    out.append("## 📅 麦当劳活动日历（数据来自 campaign-calendar）")
    out.append("")
    if not items:
        out.append("未解析到活动条目。请确认 --calendar 指向 `campaign-calendar` 的返回内容。")
        return "\n".join(out)

    groups = {"today": [], "upcoming": [], "past": []}
    for it in items:
        groups[it["group"]].append(it)
    for g in groups:
        groups[g].sort(key=lambda x: x["date"])

    if window is not None:
        limit = today + dt.timedelta(days=window)
        groups["upcoming"] = [i for i in groups["upcoming"] if dt.date.fromisoformat(i["date"]) <= limit]
        groups["past"] = [i for i in groups["past"] if dt.date.fromisoformat(i["date"]) >= today - dt.timedelta(days=window)]

    out.append("基准日：%s｜共 %d 条活动（今日 %d / 即将 %d / 往期 %d）"
               % (today.isoformat(), len(items), len(groups["today"]),
                  len(groups["upcoming"]), len(groups["past"])))
    out.append("")

    def block(title, rows):
        if not rows:
            return
        out.append("### %s" % title)
        out.append("")
        for it in rows:
            tag = "🟢 今天" if it["group"] == "today" else (
                "🔵 %d 天后" % it["delta"] if it["delta"] > 0 else "⚪ %d 天前" % (-it["delta"]))
            out.append("**%s**（%s｜%s）" % (it["title"], it["dateRaw"], tag))
            out.append("")
            txt, bits = summarize(it)
            if txt:
                out.append("- %s" % txt)
            if bits:
                out.append("- " + "；".join(bits))
            out.append("")

    block("🟢 今天进行中", groups["today"])
    block("🔵 即将开始", groups["upcoming"])
    block("⚪ 往期（最近）", groups["past"][-3:] if len(groups["past"]) > 3 else groups["past"])

    out.append("> 活动详情与参与入口以麦当劳官方渠道为准。若查询的是某一天附近的安排，"
               "可直接带上 `specifiedDate`（yyyy-MM-dd）再调一次 `campaign-calendar`。")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="整理麦当劳活动日历")
    ap.add_argument("--calendar", required=True, help="campaign-calendar 的返回落盘文件，'-' 读标准输入")
    ap.add_argument("--today", help="基准日 YYYY-MM-DD，默认取日历里的当前时间（再退化为系统日期）")
    ap.add_argument("--window", type=int, help="只看前后 N 天")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    payload = load_mcp_payload(args.calendar)
    items, parsed_now = parse_calendar(payload)

    if args.today:
        today = dt.date.fromisoformat(args.today)
    elif parsed_now:
        today = parsed_now
    else:
        today = dt.date.today()

    items = classify(items, today)

    if args.json:
        print(json.dumps({
            "today": today.isoformat(),
            "count": len(items),
            "groups": {
                "today": [i for i in items if i["group"] == "today"],
                "upcoming": [i for i in items if i["group"] == "upcoming"],
                "past": [i for i in items if i["group"] == "past"],
            },
        }, ensure_ascii=False, indent=2))
        return 0

    print(render(items, today, window=args.window))
    return 0 if items else 2


if __name__ == "__main__":
    sys.exit(main())
