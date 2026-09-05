"""Generic features of a natural-language screen reference.

These are evidence extractors for scoring, not tool routes. Spatial words,
type hints, and deictic language become features on the same ReferenceFeatures
object so resolution stays one mechanism.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from hermes.agent.application_catalog import WEB_SHORTCUTS, normalize_user_text


class SpatialSlot(StrEnum):
    FIRST = "first"
    LAST = "last"
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class ReferenceFeatures:
    raw: str
    spatial: SpatialSlot | None = None
    ordinal: int | None = None
    text_tokens: tuple[str, ...] = ()
    type_hints: frozenset[str] = frozenset()
    deictic: bool = False
    session_ref: bool = False
    quoted_literal: str | None = None
    wants_report: bool = False
    wants_navigate: bool = False

    @property
    def has_relational_language(self) -> bool:
        return bool(
            self.spatial
            or self.ordinal is not None
            or self.deictic
            or self.session_ref
        )


_SPATIAL: tuple[tuple[re.Pattern[str], SpatialSlot], ...] = (
    (re.compile(r"\b(ilk|birinci|first|en\s+[uü]st(?:teki)?|en\s+yukar[ıi])\b", re.I), SpatialSlot.FIRST),
    (re.compile(r"\b(son|sonuncu|last|en\s+alt(?:taki)?)\b", re.I), SpatialSlot.LAST),
    (re.compile(r"\b(orta(?:daki(?:ni)?|s[ıi](?:n[ıi])?|sini)?|middle|center|merkez(?:deki)?)\b", re.I), SpatialSlot.CENTER),
    (re.compile(r"\b(sol(?:daki|unda)?|left)\b", re.I), SpatialSlot.LEFT),
    (re.compile(r"\b(sa[gğ](?:daki|inda)?|right)\b", re.I), SpatialSlot.RIGHT),
    (re.compile(r"\b([uü]st(?:teki|unde)?|top|yukar[ıi]daki)\b", re.I), SpatialSlot.TOP),
    (re.compile(r"\b(alt(?:taki|inda)?|bottom|a[sş]a[gğ][ıi]daki)\b", re.I), SpatialSlot.BOTTOM),
)

_ORDINAL: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(ilk(?:ini|inden)?|birinci(?:sini)?|first)\b", re.I), 0),
    (re.compile(r"\b(ikinci(?:sini)?|second)\b", re.I), 1),
    (re.compile(r"\b([uü][cç][uü]nc[uü](?:s[uü]n[uü])?|third)\b", re.I), 2),
)

_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "video": ("video", "videoyu", "videosunu", "videolar", "videolardan"),
    "button": ("buton", "butona", "button", "d[uü][gğ]me", "d[uü][gğ]meye"),
    "window": ("pencere", "pencereyi", "window"),
    "file": ("dosya", "dosyayi", "dosyay[ıi]", "file"),
    "image": ("resim", "resmi", "g[oö]rsel", "image", "foto"),
    "input": ("kutu", "kutusu", "input", "search", "arama"),
}

_DEICTIC = re.compile(
    r"\b(ekranda|ekrandaki|g[oö]rd[uü][gğ][uü]n|gordugun|girdi[gğ]in|"
    r"[sş]u\s+an\s+ekranda|g[oö]rd[uü]klerin|bunu|şunu|sunu|onu|"
    r"şu|bu|o)\b",
    re.I,
)
_SCREEN_DEICTIC = re.compile(
    r"\b(ekranda|ekrandaki|g[oö]rd[uü][gğ][uü]n|gordugun|girdi[gğ]in|"
    r"[sş]u\s+an\s+ekranda|g[oö]rd[uü]klerin)\b",
    re.I,
)
_SESSION = re.compile(r"\b(az\s+[oö]nce|az\s+once|a[cç]t[ıi][gğ][ıi]m[ıi]z|son\s+a[cç]t)\b", re.I)
_REPORT = re.compile(
    r"\b(s[oö]yle|anlat|ba[sş]l[ıi][gğ]|title|ne\s+a[cç]t|[oö]zetle|report|tell)\b",
    re.I,
)
_NAVIGATE = re.compile(r"\b(gir|git|a[cç]|open|go|ge[cç])\b", re.I)
_ACT = re.compile(r"\b(a[cç]|tikla|tıkla|bas|izle|oynat|sec|se[cç]|click|open|play)\b", re.I)
_FIND = re.compile(r"\b(bul|find|ara|aram|goster|g[oö]ster)\b", re.I)
_QUOTE = re.compile(r"[\"“”']([^\"“”']{1,80})[\"“”']")

_FUNCTION_WORDS = frozenset(
    {
        "ve", "ile", "ilgili", "bir", "tane", "olan", "olanlardan", "en",
        "videoyu", "videosunu", "videolar", "videolardan", "video",
        "buton", "butona", "button", "pencere", "pencereyi", "window",
        "dosya", "dosyayi", "file", "resim", "resmi", "image",
        "ekranda", "ekrandaki", "gordugun", "gördüğün", "girdigin",
        "ac", "aç", "tikla", "tıkla", "bas", "izle", "oynat", "sec", "seç",
        "youtube", "chrome", "sayfa", "sayfayi", "abi", "lutfen", "lütfen",
        "the", "a", "an", "on", "in", "to", "of",
        "ilk", "birinci", "first", "son", "sonuncu", "last",
        "orta", "ortadaki", "ortasi", "ortası", "ortasini", "middle", "center",
        "sol", "soldaki", "left", "sag", "sağ", "sagdaki", "sağdaki", "right",
        "ust", "üst", "ustteki", "üstteki", "top", "alt", "alttaki", "bottom",
        "asagidaki", "aşağıdaki", "yukaridaki", "yukarıdaki",
        "kirmizi", "kırmızı", "red", "mavi", "blue",
    }
)


def extract_reference_features(text: str) -> ReferenceFeatures:
    raw = normalize_user_text(text or "").strip()
    lower = raw.casefold()

    quoted = None
    match = _QUOTE.search(raw)
    if match:
        quoted = match.group(1).strip()

    spatial = None
    for pattern, slot in _SPATIAL:
        if pattern.search(lower):
            spatial = slot
            break

    ordinal = None
    for pattern, index in _ORDINAL:
        if pattern.search(lower):
            ordinal = index
            break
    if ordinal is None:
        digit = re.search(r"\b(\d{1,2})\s*\.?(?:\b|$)", lower)
        if digit:
            value = int(digit.group(1))
            if 1 <= value <= 20:
                ordinal = value - 1

    type_hints: set[str] = set()
    for hint, forms in _TYPE_HINTS.items():
        if any(re.search(rf"\b{form}\b", lower) for form in forms):
            type_hints.add(hint)

    tokens = [
        word
        for word in re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", lower)
        if word not in _FUNCTION_WORDS and len(word) > 1
    ]

    return ReferenceFeatures(
        raw=raw,
        spatial=spatial,
        ordinal=ordinal,
        text_tokens=tuple(tokens),
        type_hints=frozenset(type_hints),
        deictic=bool(_DEICTIC.search(lower)),
        session_ref=bool(_SESSION.search(lower)),
        quoted_literal=quoted,
        wants_report=bool(_REPORT.search(lower)),
        wants_navigate=bool(_NAVIGATE.search(lower) and _catalog_site_in(lower)),
    )


def _catalog_site_in(lower: str) -> bool:
    return any(name in lower for name in WEB_SHORTCUTS)


def is_literal_click_query(message: str, extracted: str | None = None) -> bool:
    """True only when the user named visible text, not a relational reference."""
    features = extract_reference_features(message)
    if features.quoted_literal:
        return True
    if features.has_relational_language:
        return False
    if _SCREEN_DEICTIC.search(features.raw.casefold()):
        return False
    if features.type_hints:
        return False
    target = (extracted or "").strip()
    if not target:
        return False
    target_features = extract_reference_features(target)
    if target_features.has_relational_language:
        return False
    if target_features.type_hints:
        return False
    return len(target) >= 2


def requests_screen_rescan(message: str) -> bool:
    """True when the user asks to look at the screen again, not pick a listed item."""
    features = extract_reference_features(message)
    return bool(_SCREEN_DEICTIC.search(features.raw.casefold()))


def looks_like_screen_reference(message: str) -> bool:
    features = extract_reference_features(message)
    if features.quoted_literal:
        return False
    if features.spatial or features.ordinal is not None or features.session_ref:
        return True
    if _SCREEN_DEICTIC.search(features.raw.casefold()):
        return True
    if features.type_hints and not features.text_tokens:
        return True
    return False


def is_screen_perception_task(message: str) -> bool:
    """A task that must observe the screen rather than search by URL/text."""
    features = extract_reference_features(message)
    lower = features.raw.casefold()
    screen_anchor = looks_like_screen_reference(message)
    has_act = bool(_ACT.search(lower))
    has_find = bool(_FIND.search(lower))
    has_navigate = features.wants_navigate or bool(
        _NAVIGATE.search(lower) and _catalog_site_in(lower)
    )
    if screen_anchor and (has_act or features.wants_report):
        return True
    if features.type_hints and has_act and not features.quoted_literal:
        catalog_search = _catalog_site_in(lower) and (has_find or features.wants_navigate)
        if not catalog_search:
            return True
    if has_navigate and screen_anchor:
        return True
    return False
