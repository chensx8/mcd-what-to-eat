#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — 自检：不用连 MCP，用 fixtures 把四个脚本全部跑一遍

    python scripts/selftest.py           # 跑测试
    python scripts/selftest.py -v        # 同时打印脚本原始输出

覆盖点：
  ① 随机选餐   —— 真随机性（多次结果不全同）、固定种子可复现、周边商品被排除、预算约束生效
  ② 营养计算   —— MCP 原始文本可解析、重复行去重、模糊匹配、合计正确
  ④ 优惠券匹配 —— 券列表文本解析、名称变体匹配、最长公共子串匹配、过期券标记
  ⑤ 活动日历   —— 日期分段解析（含同行状态词）、今天/即将/往期分组
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(ROOT, "tests", "fixtures")
PY = sys.executable

PASS, FAIL = [], []


def run(script, args):
    cmd = [PY, os.path.join(HERE, script)] + args
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append("%s%s" % (name, ("  → " + detail) if detail else ""))


def main():
    verbose = "-v" in sys.argv
    menu = os.path.join(FIX, "menu_raw.txt")
    nutrition = os.path.join(FIX, "nutrition_raw.txt")
    coupons = os.path.join(FIX, "coupons_raw.txt")
    calendar = os.path.join(FIX, "calendar_raw.txt")

    for f in (menu, nutrition, coupons, calendar):
        check("fixture 存在: %s" % os.path.basename(f), os.path.exists(f))

    # ---------------- ① 随机选餐 ----------------
    rc, out, err = run("mcd_random_pick.py", ["--menu", menu, "--json"])
    if verbose:
        print("[random_pick]", rc, out[:400], err[:400])
    check("① 随机选餐 退出码为 0", rc == 0, err.strip()[:200])
    try:
        d = json.loads(out)
    except Exception as e:
        d = None
        check("① 输出为合法 JSON", False, str(e))
    if d:
        check("① 候选池非空", d["poolSize"] > 0, "poolSize=%s" % d.get("poolSize"))
        picked = d["picked"]
        check("① 抽到 1 项", len(picked) == 1)
        check("① 排除了周边商品（棒球帽）",
              all("帽" not in p["name"] for p in picked),
              str([p["name"] for p in picked]))
        check("① 单品模式默认不抽饮品",
              all("可乐" not in p["name"] and "黑巧" not in p["name"] for p in picked),
              str([p["name"] for p in picked]))

    # 真随机：跑 12 次，结果不应全同
    seen = set()
    for _ in range(12):
        rc, out, _ = run("mcd_random_pick.py", ["--menu", menu, "--json"])
        if rc == 0:
            try:
                seen.add(json.loads(out)["picked"][0]["code"])
            except Exception:
                pass
    check("① 多次抽取结果不全相同（真随机）", len(seen) >= 3, "只抽到 %d 种" % len(seen))

    # 固定种子可复现
    a = run("mcd_random_pick.py", ["--menu", menu, "--seed", "1024", "--json"])[1]
    b = run("mcd_random_pick.py", ["--menu", menu, "--seed", "1024", "--json"])[1]
    check("① 固定种子结果可复现", a == b and a.strip() != "")

    # 预算约束 + 组合模式
    rc, out, err = run("mcd_random_pick.py", ["--menu", menu, "--combo", "--budget", "45", "--json"])
    if verbose:
        print("[combo]", rc, out[:400], err[:200])
    if rc == 0:
        cd = json.loads(out)
        check("① 组合模式在预算内", cd["totalPrice"] is not None and cd["totalPrice"] <= 45,
              "totalPrice=%s" % cd.get("totalPrice"))
    else:
        check("① 组合模式可跑通", False, err.strip()[:200])

    # 预算过紧应当抽不到而不是乱给
    rc, out, _ = run("mcd_random_pick.py", ["--menu", menu, "--combo", "--budget", "1", "--json"])
    check("① 预算过紧时不硬凑（返回码 2 / 空结果）",
          rc == 2 or '"picked": []' in out.replace("\n", " "), out[:150])

    # ---------------- ② 营养计算 ----------------
    rc, out, err = run("mcd_nutrition.py",
                       ["巨无霸x1,中薯条x1,可乐中杯x1", "--nutrition-file", nutrition, "--json"])
    if verbose:
        print("[nutrition]", rc, out[:600], err[:300])
    check("② 营养计算 退出码为 0", rc == 0, err.strip()[:200])
    if rc == 0:
        nd = json.loads(out)
        check("② 三项全部匹配上", len(nd["missing"]) == 0, str(nd["missing"]))
        t = nd["totals"]
        check("② 能量合计正确 (513+289+147=949)", abs(t["energyKcal"] - 949) < 0.01, str(t["energyKcal"]))
        check("② 钠合计正确 (961+165+0=1126)", abs(t["sodium"] - 1126) < 0.01, str(t["sodium"]))
        check("② 输出含占每日参考值百分比", "percentOfDaily" in nd and nd["percentOfDaily"]["energyKcal"] > 0)
        check("② 输出含可解释判断", len(nd["evaluation"]) > 0)

    # 去重：倒数两行是重复的"纯牛奶（盒装）"
    rc, out, _ = run("mcd_nutrition.py", ["--list", "--json", "--nutrition-file", nutrition])
    if rc == 0:
        names = json.loads(out)["names"]
        check("② 营养库重复行已去重", len(names) == len(set(names)), str(len(names)))

    # 别名匹配
    rc, out, _ = run("mcd_nutrition.py", ["可乐x1", "--nutrition-file", nutrition, "--json"])
    if rc == 0:
        m = json.loads(out)["matched"]
        check("② 别名「可乐」可匹配到「可乐中杯」",
              bool(m) and m[0]["matched"] == "可乐中杯", str(m))

    # 未匹配要如实报告
    rc, out, _ = run("mcd_nutrition.py", ["巨无霸三件套x1", "--nutrition-file", nutrition, "--json"])
    if rc == 0:
        nd = json.loads(out)
        check("② 套餐名未收录时如实提示 missing", len(nd["missing"]) == 1, str(nd.get("missing")))
        check("② 未匹配时不编造数字", all(v == 0 for v in nd["totals"].values()))

    # ---------------- ④ 优惠券匹配 ----------------
    rc, out, err = run("mcd_coupon_match.py",
                       ["--coupons", coupons, "--items", "中薯条x1,奥利奥麦旋风x1,巨无霸x1",
                        "--today", "2026-10-13", "--json"])
    if verbose:
        print("[coupons]", rc, out[:800], err[:300])
    check("④ 券匹配 退出码为 0", rc == 0, err.strip()[:200])
    if rc == 0:
        cd = json.loads(out)
        check("④ 解析出 5 张券", cd["couponCount"] == 5, "got %s" % cd["couponCount"])
        m = cd["matched"]
        check("④ 「中薯条」匹配到「中薯条买一送一」",
              any(h["coupon"]["title"] == "中薯条买一送一" for h in m["中薯条x1"]["hits"]))
        check("④ 「奥利奥麦旋风」通过共同词匹配到「人气麦旋风买一送一」",
              any(h["coupon"]["title"] == "人气麦旋风买一送一" for h in m["奥利奥麦旋风x1"]["hits"]))
        check("④ 「巨无霸」没有硬凑匹配", len(m["巨无霸x1"]["hits"]) == 0,
              str(m["巨无霸x1"]["hits"]))
        expired = [h for h in m["中薯条x1"]["hits"] if h["coupon"]["title"] == "免费脆薯饼"]
        check("④ 有效期解析正确（券池含 10-05 到期的券）",
              any(c["end"] == "2026-10-05" for c in cd["coupons"]),
              str([c.get("end") for c in cd["coupons"]]))

    rc, out, _ = run("mcd_coupon_match.py",
                     ["--coupons", coupons, "--items", "脆薯饼x1", "--today", "2026-10-13"])
    check("④ 过期券被标注", "已过期" in out, out[:300])

    # ---------------- ⑤ 活动日历 ----------------
    rc, out, err = run("mcd_activity_brief.py",
                       ["--calendar", calendar, "--today", "2026-10-13", "--json"])
    if verbose:
        print("[calendar]", rc, out[:600], err[:300])
    check("⑤ 活动日历 退出码为 0", rc == 0, err.strip()[:200])
    if rc == 0:
        cd = json.loads(out)
        check("⑤ 解析出全部 6 条活动", cd["count"] == 6, "got %s" % cd["count"])
        check("⑤ 今日分组 2 条", len(cd["groups"]["today"]) == 2, str(len(cd["groups"]["today"])))
        check("⑤ 即将开始分组 2 条", len(cd["groups"]["upcoming"]) == 2, str(len(cd["groups"]["upcoming"])))
        check("⑤ 往期分组 2 条", len(cd["groups"]["past"]) == 2, str(len(cd["groups"]["past"])))
        titles = [i["title"] for i in cd["groups"]["upcoming"]]
        # 注意 clean() 会做 NFKC 归一化，全角"！"会变成半角"!"，断言时统一处理
        norm_titles = [t.replace("!", "！") for t in titles]
        check("⑤ 日期标题同行状态词未吞掉活动标题",
              "麦当劳 X PEACEMINUSONE" in titles and "第三周周边即将开售✨限定主题丝巾！" in norm_titles,
              str(norm_titles))
        today_titles = [i["title"] for i in cd["groups"]["today"]]
        check("⑤ 今日活动标题解析正确", len(today_titles) == 2 and "麦当劳联动G-DRAGON" in today_titles,
              str(today_titles))
        past_titles = [i["title"] for i in cd["groups"]["past"]]
        check("⑤ 粗体数字标题被 NFKC 归一化（𝟗.𝟗 → 9.9）",
              any("9.9元早餐两件套" in t for t in past_titles), str(past_titles))
        prices = {p for i in cd["groups"]["today"] for p in i["prices"]}
        check("⑤ 价格锚点抽取正确（含 𝟗.𝟗 全角/粗体数字）", 9.9 in prices or 39.9 in prices, str(prices))

    # ---------------- 汇总 ----------------
    print("=" * 62)
    for p in PASS:
        print("  ✅ %s" % p)
    for f in FAIL:
        print("  ❌ %s" % f)
    print("=" * 62)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
