#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_nutrition_snapshot.py — 维护脚本：把麦当劳 MCP 的营养数据落成快照

数据来源
--------
麦当劳 MCP Server 的工具 `list-nutrition-foods`（无需入参），返回一份
"[N]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,
   carbohydrate,sodium,calcium}" 形式的表格文本。

用法
----
1) 在 WorkBuddy 里调用 MCP 工具 `list-nutrition-foods`，把整段返回文本
   存成文件（例如 raw.txt）；
2) 执行：

   python scripts/refresh_nutrition_snapshot.py raw.txt -o data/nutrition.json

也支持直接喂 MCP 调用结果 JSON（含 {"result":{"content":[{"text": "..."}]}}）：

   python scripts/refresh_nutrition_snapshot.py call_result.json -o data/nutrition.json

为什么需要快照
--------------
营养查表是"纯数据、纯计算"的场景：一次取数、离线可复用，可以避免每次
对话都打一次网络往返，也能让营养合计在无网环境下可复现。实时数据仍然
优先——SKILL.md 要求先调 MCP，把返回文本直接交给
`scripts/mcd_nutrition.py --nutrition-file -`，快照只作为兜底。
"""

import argparse
import json
import os
import re
import sys

# 表头顺序（来自 MCP 工具输出的字段说明）
FIELDS = [
    "productName",
    "nutritionDescription",
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
]

ROW_RE = re.compile(
    r"^\s*(?P<name>[^,]+),(?P<desc>[^,]*),(?P<kj>\d+),(?P<kcal>\d+),"
    r"(?P<protein>\d+),(?P<fat>\d+),(?P<carb>\d+),(?P<sodium>\d+),(?P<calcium>\d+)\s*$"
)


def _try_json(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _unwrap_mcp(node):
    """把 MCP 调用结果 {"result":{"content":[{"text":...}]}} 拆成纯文本。"""
    if isinstance(node, dict) and "result" in node:
        node = node["result"]
    if isinstance(node, dict) and isinstance(node.get("content"), list):
        parts = [it.get("text", "") for it in node["content"] if isinstance(it, dict)]
        if parts:
            return "\n".join(parts)
    return None


def extract_text(payload: str) -> str:
    """
    输入可能是下面任意一种，统一还原成带真实换行的表格文本：

    a) list-nutrition-foods 的返回文本（内含 {"data":"[N]{...}\\n..."} 的 JSON）
    b) MCP 调用结果 JSON（{"result":{"content":[{"text": "..."}]}}）
    c) 已经是干净的表格文本
    """
    s = payload.strip()
    candidates = []
    if s.startswith("{"):
        obj = _try_json(s)
        if obj is not None:
            candidates.append(obj)
    i, j = payload.find("{"), payload.rfind("}")
    if i != -1 and j > i:
        obj = _try_json(payload[i : j + 1])
        if obj is not None:
            candidates.append(obj)

    for obj in candidates:
        # b) MCP 调用结果 → 取出 text，再往里钻一层
        inner = _unwrap_mcp(obj)
        if inner is not None:
            k, l = inner.find("{"), inner.rfind("}")
            if k != -1 and l > k:
                obj2 = _try_json(inner[k : l + 1])
                if isinstance(obj2, dict) and isinstance(obj2.get("data"), str):
                    return obj2["data"]
            return inner
        # a) 直接是接口返回体
        node = obj
        if isinstance(node, dict) and isinstance(node.get("data"), str):
            return node["data"]

    # c) 兜底：把转义换行还原成真实换行后再交给解析器
    return payload.replace("\\r\\n", "\n").replace("\\n", "\n")



def parse_rows(text: str) -> list:
    rows = []
    seen = set()
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        g = m.groupdict()
        name = g["name"].strip()
        # 原始表里出现过重复行（如"小杯玉米杯""纯牛奶（盒装）"），去重
        if name in seen:
            continue
        seen.add(name)
        desc = g["desc"].strip()
        rows.append(
            {
                "productName": name,
                "nutritionDescription": None if desc in ("", "null") else desc,
                "energyKj": int(g["kj"]),
                "energyKcal": int(g["kcal"]),
                "protein": float(g["protein"]),
                "fat": float(g["fat"]),
                "carbohydrate": float(g["carb"]),
                "sodium": float(g["sodium"]),
                "calcium": float(g["calcium"]),
            }
        )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="把麦当劳 MCP 营养数据转成 JSON 快照")
    ap.add_argument("input", help="MCP 返回的原始文本，或调用结果 JSON；'-' 表示标准输入")
    ap.add_argument("-o", "--output", default="data/nutrition.json", help="输出 JSON 路径")
    args = ap.parse_args(argv)

    if args.input == "-":
        payload = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8", errors="replace") as f:
            payload = f.read()

    rows = parse_rows(extract_text(payload))
    if not rows:
        print("未解析到任何营养数据行，请检查输入是否为 list-nutrition-foods 的返回", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    doc = {
        "_source": "McDonald's MCP Server / list-nutrition-foods",
        "_unit": {
            "energyKj": "千焦 kJ",
            "energyKcal": "千卡 kcal",
            "protein": "克 g",
            "fat": "克 g",
            "carbohydrate": "克 g",
            "sodium": "毫克 mg",
            "calcium": "毫克 mg",
        },
        "count": len(rows),
        "items": rows,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("已写入 %s（%d 条）" % (args.output, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
