"""Type rules and relevance filters.

Types come from the *source*, not from keywords:
  release / announcement  only official sources (MusicBrainz, the artist's own YouTube channel)
  concert                 only concert sources (setlist.fm, Ticketmaster)
  news                    everything third-party (Google News, Reddit); `topic` is a keyword hint only
"""

import re

# ---------- topic hint for third-party items (stored in payload.topic, not used as type) ----------
TOPIC_RULES = [
    ("concert", r"\b(world tour|tour dates?|concert|encore|fan ?meeting|fanmeet|festival lineup|headlin\w+|stadium|arena tour)\b"),
    ("appearance", r"\b(talk show|variety|tonight show|fallon|kimmel|colbert|snl|saturday night live|interview|guest|appear\w*|cameo|ambassador|campaign|endorse\w*|commercial|photoshoot|pictorial|cover of|vogue|elle|collab\w*|featuring)\b"),
    ("announcement", r"\b(announc\w+|teaser|coming soon|to release|will release|set to|confirm\w*|upcoming|pre-?order|tracklist|schedule poster|comeback date)\b"),
    ("release", r"\b(m/?v|official (music )?video|new (single|album|ep|song)|releases?d?|drops?|out now|comeback|debut|mini ?album|full album|digital single|ost)\b"),
]
_TOPIC = [(t, re.compile(p, re.IGNORECASE)) for t, p in TOPIC_RULES]


def topic(text: str) -> str:
    for kind, rx in _TOPIC:
        if rx.search(text or ""):
            return kind
    return "general"


_NOT_OFFICIAL_RX = re.compile(r"\b(behind( the scenes)?|making( film| of)?|reaction|reacts?|bts film|vlog|sketch)\b", re.I)
# Official uploads title-case or capitalize these ("MV Teaser", "SEASON'S GREETINGS TRAILER");
# lowercase use is conversational ("thought this was a movie trailer").
_ANNOUNCE_RX = re.compile(r"\b(Teaser|TEASER|Trailer|TRAILER|Highlight Medley|HIGHLIGHT MEDLEY|Concept Film|"
                          r"CONCEPT FILM|Coming Soon|COMING SOON|Spoiler|SPOILER|Schedule|SCHEDULE)\b")
_RELEASE_RX = re.compile(r"\b(m/?v|official (music )?video|official audio|lyric video|visualizer)\b", re.I)


def classify_video(title: str) -> str:
    """Official YouTube uploads: teasers are announcements, MVs/official audio are releases.

    Teasers are checked first so "MV Teaser" is an announcement, not a release.
    """
    t = title or ""
    if _NOT_OFFICIAL_RX.search(t):
        return "video"
    if _ANNOUNCE_RX.search(t):
        return "announcement"
    if _RELEASE_RX.search(t):
        return "release"
    return "video"


# ---------- relevance: is this headline really about this artist? ----------
MUSIC_RX = re.compile(
    r"\b(k-?pop|j-?pop|kpop|album|ep|song|songs|ost|m/?v|music video|comeback|debut\w*|concert|tour|"
    r"fan ?meet\w*|idol|singer|rapper|vocalist|band|boy ?group|girl ?group|billboard|hot 100|melon|"
    r"circle chart|oricon|spotify|music charts?|grammys?|"
    r"cma|acm|opry|nashville|country music|country star|perform\w*|festival|lyrics?|duet|collab\w*|encore|"
    r"setlist|mama awards|music bank|inkigayo|m countdown|music core|show champion|weverse|jyp|"
    r"sm entertainment|yg entertainment|hybe|starship|pledis|big hit|ador|belift|source music|kakao|"
    r"teaser|showcase|music)\b",
    re.IGNORECASE,
)


def has_word(text: str, word: str, case: bool = False) -> bool:
    if not word:
        return False
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, 0 if case else re.IGNORECASE) is not None


def _stylized(name: str) -> bool:
    """ALL-CAPS stylized names (TWICE, MINA, STAYC) are distinctive when matched case-sensitively."""
    letters = [c for c in name if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters if c.isascii())


def relevant(title: str, names: list[str], related: list[str], strict: bool = True,
             member: bool = False) -> bool:
    """names: the artist's own names; related: parent group / member names.

    Always: the headline must name the artist.
    strict (default): also needs context — a related artist named, the ALL-CAPS stylized name
    matched exactly, or (not for members) a music word.
    member: members/sub-units ("MINA", "Joy") need the group named or the exact stylized name;
    a music word alone is not enough (e.g. the Italian singer Mina).
    """
    if not any(has_word(title, n) for n in names):
        return False
    if not strict:
        return True
    if any(has_word(title, r) for r in related):
        return True
    if any(_stylized(n) and has_word(title, n, case=True) for n in names):
        return True
    return not member and bool(MUSIC_RX.search(title))


# ---------- Reddit: keep update/news posts, drop fan content ----------
_UPDATE_RX = re.compile(
    r"^\s*[\[(]?\d{6}[\])]?(?!\d)|\b(official|announce\w*|teaser|trailer|comeback|tracklist|concept (photo|film)|"
    r"m/?v|music video|schedule|news|article|release date|pre-?order)\b",
    re.IGNORECASE,
)
_JUNK_RX = re.compile(
    r"\b(fan ?art|fanart|meme|memes|lmao|lol|discussion|question|help|unpopular opinion|appreciation|poll|rant|"
    r"haul|my collection|photocards?|fanfic|what happens when|anyone else|does anyone|edit|fancam|shitpost|"
    r"daily thread|weekly thread|megathread)\b",
    re.IGNORECASE,
)


def is_update_post(title: str) -> bool:
    return bool(_UPDATE_RX.search(title or "")) and not _JUNK_RX.search(title or "")
