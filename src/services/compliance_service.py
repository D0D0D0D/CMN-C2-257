"""AgentCore Platform v1.0 — 景表法/優良誤認 screening (CMN-C2-257)."""

# Shared by document_gen (execute(): first-pass screening, writes
# compliance_flags into State) and post_process (execute(): final re-check;
# _extra_security_gate_output(): validation-only critical block).
# Detection writes State via execute() return values ONLY — gate hooks are
# validation/blocking, never State writers (docs/02_design.md S-3).

from __future__ import annotations

import re

# (pattern, reason, severity). "critical" = 根拠なき比較優良表示のおそれ →
# blocked at the post_process S-3 gate. "warn" = flagged for human review.
_RULES: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"業界\s*No\.?\s*1|業界第?一位|日本一|世界一|世界初|シェア\s*No\.?\s*1|シェア第?一位", re.IGNORECASE),
        "根拠なき比較優良表示のおそれ（景表法・優良誤認）",
        "critical",
    ),
    (
        re.compile(r"他社(?:製品|サービス)?(?:より|と比べて).{0,12}(?:優れ|上回|勝|優位)"),
        "比較優良表示 — 客観的根拠の明示が必要（景表法）",
        "critical",
    ),
    (
        re.compile(r"必ず|絶対に?|100\s*[%％]\s*(?:保証|達成|削減|向上)|完全に(?:解決|防止|排除)"),
        "断定的表現 — 効果を保証する表現は優良誤認のおそれ",
        "warn",
    ),
    (
        re.compile(r"業界最安|最安値|最も安い|No\.?\s*1\s*の(?:低価格|コスト)"),
        "有利誤認のおそれ（価格の最上級表現）",
        "warn",
    ),
]

_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def scan_text(text: str) -> list[dict]:
    """Scan Markdown text; returns [{"section","expression","reason","severity"}]."""
    if not text:
        return []
    headings = [(m.start(), m.group(1).strip()) for m in _HEADING.finditer(text)]
    flags: list[dict] = []
    for pattern, reason, severity in _RULES:
        for m in pattern.finditer(text):
            section = ""
            for pos, title in headings:
                if pos <= m.start():
                    section = title
                else:
                    break
            flags.append(
                {
                    "section": section,
                    "expression": m.group(0),
                    "reason": reason,
                    "severity": severity,
                }
            )
    return flags


def critical_flags(flags: list[dict]) -> list[dict]:
    return [f for f in flags if f.get("severity") == "critical"]


def merge_flags(*flag_lists: list[dict]) -> list[dict]:
    """Merge flag lists, deduping on (section, expression, reason)."""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for flags in flag_lists:
        for f in flags or []:
            key = (f.get("section"), f.get("expression"), f.get("reason"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(f)
    return merged
