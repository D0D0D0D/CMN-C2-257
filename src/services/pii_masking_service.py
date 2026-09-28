"""AgentCore Platform v1.0 — JP personal-name masking (CMN-C2-257)."""

# The framework default S-2 gate masks contacts (email/phone/etc.) but its
# personal-name pattern is Latin-script only — Japanese names pass through.
# This helper is shared by:
#   pre_process  S-2 _extra hook (hearing notes, before the LLM sees them)
#   post_process S-3 _extra hook (defence-in-depth: names arriving via KB
#     content — Data Contract §7 makes PII removal the ingestion side's
#     duty, so this is a second line, not the primary control)

from __future__ import annotations

import re

# Japanese personal name + honorific (担当者名など)。The honorific anchor keeps
# precision high; the name part is kanji/katakana only (hiragana runs are
# particles/verbs, not names), and a following kanji blocks compounds
# (仕様書, 様子, 氏名, ...).
_JP_NAME_HONORIFIC = re.compile(
    r"([一-龥々ァ-ヶー]{1,6})(様|さん|氏|殿)(?![一-龥々])"
)
# Common non-name 〜様/〜さん compounds that survive the regex (e.g. 「仕様を」).
_HONORIFIC_STOPWORDS = (
    "仕様", "同様", "多様", "一様", "模様", "異様", "態様",
    "客様", "皆様", "王様", "神様", "奥様", "皆さん",
)


def mask_jp_names(text: str) -> str:
    """Mask Japanese personal names anchored by honorifics (様/さん/氏/殿)."""

    def _repl(m: re.Match) -> str:
        name, honorific = m.group(1), m.group(2)
        if (name[-1] + honorific) in _HONORIFIC_STOPWORDS:
            return m.group(0)
        return "[MASKED]" + honorific

    return _JP_NAME_HONORIFIC.sub(_repl, text)
