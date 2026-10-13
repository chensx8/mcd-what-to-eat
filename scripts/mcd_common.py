#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_common.py — 本 Skill 各脚本共用的解析 / 格式化工具

麦当劳 MCP 的返回有两层包装：
  1) 工具调用结果：{"result": {"content": [{"type": "text", "text": "<正文>"}]}}
  2) 正文里往往又是「人读的说明 + 一段 JSON 原文」，例如
     "# API Response Information ... Original Response\n\n{"success":true,...,"data":{...}}"
所以 load_mcp_payload() 会依次尝试：整段 JSON → 调用结果 → 正文里的 JSON → 兜底的转义还原。
"""

import json
import os
import re
import sys
import unicodedata

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# 成人每日营养素参考值（用于"占每日参考值百分比"，非医嘱，仅作决策辅助）
DAILY_REFERENCE = {
    "energyKcal": 2000.0,   # 千卡，以 2000 kcal 膳食为基准
    "protein": 60.0,        # 克
    "fat": 60.0,            # 克（约占总能量 27%）
    "carbohydrate": 300.0,  # 克
    "sodium": 2000.0,       # 毫克（每日建议上限）
    "calcium": 800.0,       # 毫克
}

NUTRIENT_LABEL = {
    "energyKcal": ("能量", "kcal"),
    "energyKj": ("能量", "kJ"),
    "protein": ("蛋白质", "g"),
    "fat": ("脂肪", "g"),
    "carbohydrate": ("碳水化合物", "g"),
    "sodium": ("钠", "mg"),
    "calcium": ("钙", "mg"),
}


# --------------------------------------------------------------------------
# 文本 / 数值
# --------------------------------------------------------------------------

def norm(s):
    """归一化：全角转半角、去空格、去常见装饰字符、小写。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[【】\[\]（）()「」『』\"'“”‘’*·、,，。.：:!！?？\-—_/\\]", "", s)
    return s.lower()


def parse_qty(expr):
    """
    解析 "巨无霸x2" / "巨无霸*2" / "巨无霸 2份" / "巨无霸" → (名称, 数量)
    """
    expr = str(expr).strip()
    m = re.search(r"[xX*×]\s*(\d+)\s*$", expr)
    if m:
        return expr[: m.start()].strip(), int(m.group(1))
    m = re.search(r"(\d+)\s*[份个杯块支根]$", expr)
    if m:
        return expr[: m.start()].strip(), int(m.group(1))
    m = re.match(r"^(\d+)\s*(.+)$", expr)
    if m:
        return m.group(2).strip(), int(m.group(1))
    return expr, 1


def split_items(s):
    """把 "A x1, B x1；C" 拆成条目列表。"""
    parts = re.split(r"[,，;；\n]+", str(s))
    return [p.strip() for p in parts if p.strip()]


def money(v):
    try:
        return "¥%.2f" % float(v)
    except (TypeError, ValueError):
        return "—"


def fmt_num(v, unit=""):
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        return "%d%s" % (round(v), unit)
    return ("%.1f" % v) + unit


# --------------------------------------------------------------------------
# MCP 返回解析
# --------------------------------------------------------------------------

def _try_json(s):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _unwrap_result(node):
    """剥掉 MCP 的 {"result":{"content":[{"text":...}]}} 包装，返回正文文本。"""
    if not isinstance(node, dict):
        return None
    if "result" in node:
        node = node["result"]
    if isinstance(node, dict) and isinstance(node.get("content"), list):
        chunks = [it.get("text", "") for it in node["content"] if isinstance(it, dict)]
        if chunks:
            return "\n".join(chunks)
    if isinstance(node, dict) and isinstance(node.get("text"), str):
        return node["text"]
    return None


def _scan_json(text):
    """
    在一段"人读说明 + JSON 原文"的文本里，找出真正能解析的那个 JSON 对象。

    不能简单取「第一个 { 到最后一个 }」——MCP 的返回里前面还有
    `data.meals.{code}.withOrder` 这类含花括号的**文档说明**，会把范围切歪。
    这里用 raw_decode 逐个 { 起点尝试，取第一个带 success/data 的字典。
    """
    decoder = json.JSONDecoder()
    best = None
    for m in re.finditer(r"[{\[]", text):
        i = m.start()
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and ("data" in obj or "success" in obj):
            best = obj
            if "data" in obj:
                return obj  # 找到业务体就直接收工
        elif isinstance(obj, list) and best is None:
            best = obj
    return best


def _dig_payload(obj, depth=0):
    """
    在解析出来的对象里找到"业务体"。
    - {"success":..., "data": {...}}        → data（dict）
    - "# 说明...\n{json}" 这种文本           → 继续往下钻
    """
    if depth > 4 or obj is None:
        return None

    if isinstance(obj, str):
        if obj.lstrip().startswith("{") or obj.lstrip().startswith("["):
            inner = _try_json(obj)
            if inner is not None:
                return _dig_payload(inner, depth + 1)
        scanned = _scan_json(obj)
        if scanned is not None:
            return _dig_payload(scanned, depth + 1)
        # 兜底：把转义换行还原后再试一次
        unescaped = obj.replace("\\r\\n", "\n").replace("\\n", "\n")
        if unescaped != obj:
            scanned = _scan_json(unescaped)
            if scanned is not None:
                return _dig_payload(scanned, depth + 1)
        return obj

    if isinstance(obj, list):
        return obj

    if isinstance(obj, dict):
        # 已经是业务体：带 data 字段
        if "data" in obj and isinstance(obj["data"], (dict, list)):
            return obj["data"]
        if "data" in obj and isinstance(obj["data"], str):
            inner = _try_json(obj["data"])
            if inner is not None:
                return _dig_payload(inner, depth + 1)
            return obj["data"]
        # MCP 包装层
        text = _unwrap_result(obj)
        if text is not None:
            return _dig_payload(text, depth + 1)
        return obj

    return obj


def load_mcp_payload(src):
    """
    读入一份 MCP 工具输出的落盘内容（文件路径 / '-' 标准输入 / 原始字符串），
    还原成 Python 对象（dict 或 list）。无法解析时返回 None。
    """
    if src is None:
        return None
    if isinstance(src, (dict, list)):
        return _dig_payload(src)
    if src == "-":
        raw = sys.stdin.read()
    elif isinstance(src, str) and ("\n" in src or len(src) > 260 or not os.path.exists(src)):
        raw = src
    else:
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()

    raw = raw.strip()
    obj = _try_json(raw)
    if obj is None and not raw:
        return None
    if obj is None:
        res = _dig_payload(raw)
        return res
    return _dig_payload(obj)


# --------------------------------------------------------------------------
# 菜单（query-meals）解析
# --------------------------------------------------------------------------

def flatten_menu(payload):
    """
    把 query-meals 的返回拍平成 [{code,name,currentPrice,originalPrice,category,tags,discountType}]。
    payload 可以直接是 data 体（含 categories + meals），也可以是一份简单列表。
    """
    if payload is None:
        return []

    # 已经是简单列表
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "name" in payload[0]:
        out = []
        for it in payload:
            out.append(
                {
                    "code": str(it.get("code", "")),
                    "name": it.get("name", ""),
                    "currentPrice": _to_float(it.get("currentPrice", it.get("price"))),
                    "originalPrice": _to_float(it.get("originalPrice")),
                    "category": it.get("category", ""),
                    "tags": it.get("tags") or [],
                    "discountType": it.get("discountType"),
                }
            )
        return [x for x in out if x["name"]]

    if not isinstance(payload, dict):
        return []

    details = payload.get("meals") or {}
    rows = []
    seen = set()

    for cat in payload.get("categories") or []:
        cat_name = (cat.get("name") or "").replace("\n", " ")
        for meal in cat.get("meals") or []:
            code = str(meal.get("code", ""))
            if not code or code in seen:
                continue
            d = details.get(code) or {}
            name = d.get("name")
            if not name:
                continue
            seen.add(code)
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "currentPrice": _to_float(d.get("currentPrice")),
                    "originalPrice": _to_float(d.get("originalPrice")),
                    "category": cat_name,
                    "tags": meal.get("tags") or [],
                    "discountType": d.get("discountType"),
                }
            )

    # 兜底：categories 里没覆盖到的商品也带上
    for code, d in details.items():
        if code in seen or not isinstance(d, dict) or not d.get("name"):
            continue
        seen.add(code)
        rows.append(
            {
                "code": code,
                "name": d["name"],
                "currentPrice": _to_float(d.get("currentPrice")),
                "originalPrice": _to_float(d.get("originalPrice")),
                "category": "",
                "tags": [],
                "discountType": d.get("discountType"),
            }
        )
    return rows


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 名称匹配
# --------------------------------------------------------------------------

# 口语别名 → 营养库 / 菜单里的标准叫法
ALIASES = {
    "巨无霸": "巨无霸",
    "中可": "可乐中杯",
    "大可": "可乐大杯",
    "小可": "可乐小杯",
    "可乐": "可乐中杯",
    "无糖可乐": "无糖可口可乐中杯",
    "零度可乐": "无糖可口可乐中杯",
    "薯条": "中薯条",
    "大薯": "大薯条",
    "中薯": "中薯条",
    "小薯": "小薯条",
    "板烧": "板烧鸡腿堡",
    "麦辣鸡腿堡": "麦辣鸡腿汉堡",
    "麦辣鸡腿汉堡": "麦辣鸡腿汉堡",
    "麦香鸡汉堡": "麦香鸡",
    "麦香鱼汉堡": "麦香鱼",
    "麦乐鸡": "麦乐鸡5块",
    "鸡块": "麦乐鸡5块",
    "冰淇淋": "圆筒冰淇淋",
    "圆筒": "圆筒冰淇淋",
    "甜筒": "圆筒冰淇淋",
    "新地": "朱古力新地",
    "麦旋风": "奥利奥麦旋风",
    "玉米杯": "大杯玉米杯",
    "咖啡": "鲜萃咖啡",
    "美式": "热美式中杯",
    "热美式咖啡": "热美式中杯",
    "牛奶": "纯牛奶（盒装）",
    "可乐中杯": "可乐中杯",
    "脆薯饼": "脆薯饼",
    "薯饼": "脆薯饼",
}


# 套餐类后缀：营养库只收录单品，遇到这些词要"宁缺毋滥"，
# 不能拿"巨无霸三件套"去套"巨无霸"的数字——会严重低估整餐热量。
COMBO_KW = ["套餐", "三件套", "四件套", "两件套", "双人餐", "单人餐", "随心选",
            "随心配", "拼盘", "组合", "任选", "全家桶", "BFF"]


def match_name(query, candidates, aliases=None, cutoff=0.62, strict_combo=False):
    """
    在候选名称里找最匹配的一个。
    返回 (名称, 得分, 备注)；找不到返回 (None, 0.0, 原因)

    strict_combo=True 时（营养计算用）：形如"XX三件套/XX套餐"的查询如果对不上
    带同样套餐词的条目，就直接判为未收录，交由调用方提示用户先拆解套餐。
    """
    import difflib

    aliases = aliases if aliases is not None else ALIASES
    q_raw = str(query).strip()
    q = norm(q_raw)
    if not q:
        return None, 0.0, "空名称"

    index = {}
    for c in candidates:
        index.setdefault(norm(c), c)

    # 1) 精确
    if q in index:
        return index[q], 1.0, "精确匹配"

    # 1.5) 套餐保护：套餐名不能退化成主食名
    if strict_combo and any(norm(k) in q for k in COMBO_KW):
        if q in aliases:
            target = norm(aliases[q])
            if target in index:
                return index[target], 0.99, "别名匹配（%s）" % aliases[q]
        return None, 0.0, "疑似套餐组合，营养库只收录单品，请先用 query-meal-detail 拆解"

    # 2) 别名
    if q in aliases:
        target = norm(aliases[q])
        if target in index:
            return index[target], 0.99, "别名匹配（%s）" % aliases[q]
        for k, v in index.items():
            if target and (target in k or k in target):
                return v, 0.95, "别名匹配（%s）" % aliases[q]

    # 3) 包含
    contains = [c for k, c in index.items() if k and (q in k or k in q)]
    if contains:
        contains.sort(key=lambda x: (abs(len(norm(x)) - len(q)), len(x)))
        return contains[0], 0.9, "包含匹配"

    # 4) 模糊
    close = difflib.get_close_matches(q, list(index.keys()), n=1, cutoff=cutoff)
    if close:
        return index[close[0]], round(difflib.SequenceMatcher(None, q, close[0]).ratio(), 3), "模糊匹配"

    return None, 0.0, "未找到"


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def emit(obj, as_json=False, human=None):
    """统一出口：--json 时输出 JSON，否则输出人读文本。"""
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(human if human is not None else json.dumps(obj, ensure_ascii=False, indent=2))
