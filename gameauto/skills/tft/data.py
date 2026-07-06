"""TFT S17 棋子/羁绊数据 + 羁绊计算。

数据来自 GoldenShovel-S17-Skill, 转成标准 JSON (data/champions.json, data/traits.json)。
OCR 识别出的棋子名 → 查表得费用/羁绊 → compute_active_traits() 算当前激活羁绊。

也为大模型备料: champions/traits 的 description/effect 可直接喂 LLM 做长期决策。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_cache: dict[str, dict] = {}


def load_champions() -> dict:
    """{棋子名: {cost, traits, skill, description}} + aliases。"""
    if "champions" not in _cache:
        _cache["champions"] = json.loads((_DATA_DIR / "champions.json").read_text(encoding="utf-8"))
    return _cache["champions"]


def load_traits() -> dict:
    """{羁绊名: {type, tiers, effect, champions}}。"""
    if "traits" not in _cache:
        _cache["traits"] = json.loads((_DATA_DIR / "traits.json").read_text(encoding="utf-8"))
    return _cache["traits"]


def resolve_name(name: str) -> str | None:
    """OCR 文本 → 标准棋子名。先查别名(OCR纠错), 再精确匹配; 找不到返回 None。"""
    data = load_champions()
    aliases = data.get("aliases", {})
    champs = data["champions"]
    if name in champs:
        return name
    if name in aliases:
        return aliases[name]
    return None


def champion_info(name: str) -> dict | None:
    """OCR 文本 → 棋子信息 {cost, traits, skill, description}。"""
    real = resolve_name(name)
    if not real:
        return None
    return load_champions()["champions"][real]


def compute_active_traits(champion_names: list[str]) -> list[dict]:
    """给定棋子名列表(OCR结果), 算当前激活的羁绊。

    返回 [{trait, count, tier, next_tier, effect}, ...], 按档位降序。
    未识别的名字(不在表里)自动跳过。
    """
    champs = load_champions()["champions"]
    traits_db = load_traits()["traits"]

    counts: Counter[str] = Counter()
    unknown = []
    for name in champion_names:
        real = resolve_name(name)
        if not real:
            unknown.append(name)
            continue
        for t in champs[real].get("traits", []):
            counts[t] += 1

    active = []
    for tname, cnt in counts.items():
        t = traits_db.get(tname)
        if not t:
            continue
        tiers = sorted(t.get("tiers", []))
        if not tiers:
            continue
        reached = [ti for ti in tiers if cnt >= ti]
        if not reached:
            continue
        idx = len(reached) - 1
        active.append({
            "trait": tname,
            "count": cnt,
            "tier": reached[-1],
            "next_tier": tiers[idx + 1] if idx + 1 < len(tiers) else None,
            "effect": t.get("effect", ""),
        })
    active.sort(key=lambda x: x["tier"], reverse=True)
    return active


def traits_for_llm() -> dict:
    """给长期 LLM 的精简羁绊知识 (名字 + 档位 + 效果)。"""
    return {n: {"tiers": t["tiers"], "effect": t["effect"]}
            for n, t in load_traits()["traits"].items()}
