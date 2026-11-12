"""Keyword-rule classifier for free-text items (news, Reddit posts, video titles).

First match wins, so order matters: specific before general.
"""

import re

RULES = [
    ("concert", r"\b(world tour|tour dates?|concert|encore|fan ?meeting|fanmeet|festival lineup|headlin\w+|stadium|arena tour)\b"),
    ("appearance", r"\b(talk show|variety|tonight show|fallon|kimmel|colbert|snl|saturday night live|interview|guest|appear\w*|cameo|ambassador|campaign|endorse\w*|commercial|cf\b|photoshoot|pictorial|cover of|vogue|elle|collab\w*|featuring)\b|\b(feat|ft)\."),
    ("announcement", r"\b(announc\w+|teaser|coming soon|to release|will release|set to|confirm\w*|upcoming|pre-?order|tracklist|schedule poster|comeback date|d-\d+)\b"),
    ("release", r"\b(m/?v|official (music )?video|new (single|album|ep|song)|releases?d?|drops?|out now|comeback|debut|mini ?album|full album|digital single|ost)\b"),
]
_COMPILED = [(t, re.compile(p, re.IGNORECASE)) for t, p in RULES]


def classify(text: str, default: str = "news") -> str:
    for kind, rx in _COMPILED:
        if rx.search(text or ""):
            return kind
    return default


def classify_video(title: str) -> str:
    """YouTube uploads: MVs/official videos are releases, everything else is a video."""
    t = title or ""
    if re.search(r"\b(m/?v|official (music )?video|official audio|lyric video|visualizer)\b", t, re.I):
        return "release"
    if re.search(r"\b(teaser|trailer|highlight medley|concept film|coming soon)\b", t, re.I):
        return "announcement"
    return "video"
