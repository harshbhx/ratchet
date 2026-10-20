"""Field comparators: when two answers to a task agree, field by field (FR-2.14, ADR 0021).

A task names the fields that must agree (`tasks.Task.fields`, a tuple of `FieldSpec`). Each field has a path into the
answer and a type. The type turns a raw value into a normalised one once per answer (`FieldSpec.normalize`, `INVALID`
when it cannot) and compares two normalised values (`FieldSpec.match`). The v0.4 rule compared top-level JSON values
exactly, so 1 and 1.0 disagreed, "1,234.50" was not 1234.5, a list in another order was another answer, and `{}` agreed
with itself (every key None == None). Types fix the first three; `required` fixes the last.

Standard library only, no I/O: every student sample is normalised here on the request path, so each type is linear in
the size of its value and one answer may hold at most `MAX_RECORDS` records at any depth. Records pair by key or by
position in linear time; without either, by their fields without a tolerance in linear time, and by the fields with one
through a maximum bipartite matching, which is quadratic in the records of a group (give records a `key` when they
have one).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import MISSING as _NO_DEFAULT
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any


class _Sentinel:
    """A named marker that is never equal to a value an answer can hold."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


MISSING: Any = _Sentinel("MISSING")  # a path the answer does not have
INVALID: Any = _Sentinel("INVALID")  # a value, or a whole answer, that cannot be compared

# Field types. `similarity` and `tool_calls` are reserved for later releases: a spec that names them is refused, so no
# task silently compares free text or tool calls with the wrong rule.
TYPES = (
    "exact",
    "label",
    "bool",
    "number",
    "ordinal",
    "text",
    "date",
    "language",
    "set",
    "records",
    "ranking",
    "similarity",
    "tool_calls",
)
RESERVED_TYPES = frozenset({"similarity", "tool_calls"})
# Types whose normalised value compares by equality (no tolerance), so it can be a set element or a record key.
SCALAR_TYPES = frozenset({"exact", "label", "bool", "number", "ordinal", "text", "date", "language"})

MAX_FIELDS = 64  # per task, counting the fields inside records
MAX_DEPTH = 4  # records inside records
MAX_VALUES = 2000  # values, aliases, order entries, suffixes or empty values of one field
MAX_RECORDS = 200  # records in one answer, at any depth (pairing records with a tolerance is quadratic in them)
MAX_ITEMS = 2000  # elements of one set or ranking
MAX_SCALAR_CHARS = 100  # a number, date or language written longer than this is not one

# Sentence punctuation a label loses at its end ("Billing." is "billing"). Not every punctuation mark: "c#" stays.
_TRAILING = frozenset(".,;:!?…。．，、；：！？")
_LABEL_SEP = re.compile(r"[\s_\-‐-―]+")


def _rstrip(text: str, chars: frozenset[str]) -> str:
    """`text` without a trailing run of `chars` and whitespace, in any mix. Linear (a regex anchored at the end is not)."""
    i = len(text)
    while i and (text[i - 1] in chars or text[i - 1].isspace()):
        i -= 1
    return text[:i]


def label_text(text: str) -> str:
    """A label as compared: NFKC, case-folded, trailing sentence punctuation dropped, and runs of whitespace, `_` and `-`
    collapsed into one `_`. 'Very High.' and 'very-high' are both 'very_high'."""
    t = _rstrip(unicodedata.normalize("NFKC", text).casefold().strip(), _TRAILING)
    return _LABEL_SEP.sub("_", t)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _hashable(v: Any) -> bool:
    try:
        hash(v)
    except TypeError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------------
# Numbers

# Active ISO 4217 codes (and HRK, retired in 2023), accepted before or after a number in capitals: "USD 12", "12.50 EUR".
CURRENCY_CODES = frozenset(
    """AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE
    CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD
    HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD
    MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON
    RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX
    USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD XPF XPT XSU XUA YER ZAR ZMW
    ZWL""".split()
)
_CODE_PREFIX = re.compile(r"([A-Z]{3})(?![A-Za-z])")
_CODE_SUFFIX = re.compile(r"(?<![A-Za-z])([A-Z]{3})$")
_LETTERS_THEN_SYMBOL = re.compile(r"[A-Za-z]{1,2}(?=\S)")  # "US$", "HK$", "R$": letters glued to a currency symbol
_SIGNS = {"-": -1.0, "+": 1.0, "−": -1.0}
_EXP = r"(?:[eE][+-]?\d+)?"


def _number_pattern(decimal: str) -> re.Pattern[str]:
    """The digits of a number written with `decimal` as its decimal mark: plain (with an exponent), or grouped in
    thousands by one separator used throughout (',' or '.' opposite the decimal mark, a space or an apostrophe), or for
    a '.' decimal also the Indian grouping (12,34,567). A group of other than 3 digits is refused: '1,5' under '.'
    is ambiguous, so it is not a number."""
    d = re.escape(decimal)
    sep = "[,' ]" if decimal == "." else "[.' ]"
    plain = rf"\d+(?:{d}\d*)?{_EXP}|{d}\d+{_EXP}"
    grouped = rf"\d{{1,3}}(?P<s>{sep})\d{{3}}(?:(?P=s)\d{{3}})*(?:{d}\d+)?"
    alts = [plain, grouped]
    if decimal == ".":
        alts.append(rf"\d{{1,2}}(?:,\d{{2}})+,\d{{3}}(?:{d}\d+)?")
    return re.compile("|".join(f"(?:{a})" for a in alts))


_NUMBER = {".": _number_pattern("."), ",": _number_pattern(",")}


def parse_number(text: str, decimal: str = ".", percent: str = "number") -> float | Any:
    """The value of a number written as text, or INVALID. Accepts a sign or '(12.00)' for a negative, currency symbols
    (Unicode category Sc) and an ISO 4217 code before or after it, thousands separators that fit `decimal`, and a
    trailing '%' (with `percent` 'fraction', '12%' is 0.12). NaN and infinities are INVALID."""
    t = unicodedata.normalize("NFKC", text).strip()
    if not t or len(t) > MAX_SCALAR_CHARS:
        return INVALID
    sign = None
    if t.startswith("(") and t.endswith(")"):
        sign, t = -1.0, t[1:-1].strip()
    pct = False
    if t.endswith(("%", "٪")):
        pct, t = True, t[:-1].strip()
    for _ in range(8):  # currency marks and a sign, in any order around the digits; each pass removes at least one
        before = t
        if t and unicodedata.category(t[0]) == "Sc":
            t = t[1:].lstrip()
        elif t and unicodedata.category(t[-1]) == "Sc":
            t = t[:-1].rstrip()
        elif (m := _CODE_PREFIX.match(t)) and m.group(1) in CURRENCY_CODES:
            t = t[3:].lstrip()
        elif (m := _CODE_SUFFIX.search(t)) and m.group(1) in CURRENCY_CODES:
            t = t[:-3].rstrip()
        elif (m := _LETTERS_THEN_SYMBOL.match(t)) and len(t) > m.end() and unicodedata.category(t[m.end()]) == "Sc":
            t = t[m.end() + 1 :].lstrip()
        elif t[:1] in _SIGNS and sign is None:
            sign, t = _SIGNS[t[:1]], t[1:].lstrip()
        if t == before:
            break
    m = _NUMBER[decimal].fullmatch(t)
    if m is None:
        return INVALID
    sep = m.group("s")
    digits = t.replace(sep, "") if sep else (t.replace(",", "") if decimal == "." and "," in t else t)
    try:
        value = float(digits.replace(decimal, ".") if decimal != "." else digits)
    except (ValueError, OverflowError):
        return INVALID
    if not math.isfinite(value):
        return INVALID
    if sign is not None:
        value *= sign
    if pct and percent == "fraction":
        value /= 100.0
    return value


# ---------------------------------------------------------------------------------------------------------------------
# Dates

_MONTHS = {
    name: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_ISO = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:[t ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?\s*(z|[+-]\d{2}(?::?\d{2})?)?)?"
)
_ORDINAL_SUFFIX = re.compile(r"(?<=\d)(?:st|nd|rd|th)\b")
_ABBREVIATION_DOT = re.compile(r"(?<=[a-z])\.")
_DATE_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2}) ([a-z]+) (\d{4})"), "dby"),
    (re.compile(r"([a-z]+) (\d{1,2}) (\d{4})"), "bdy"),
    (re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})"), "dmy"),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), "slash"),
)


def parse_date(text: str, day_first: bool = False) -> date | datetime | None:
    """A date (or, for an ISO datetime, a datetime) written in one of the accepted forms, or None. English month names
    are read here, not by `strptime`, whose `%b` follows the process locale. Ordinal suffixes ('3rd') and commas are
    ignored. '03/10/2026' is 10 March, or 3 October with `day_first`."""
    t = unicodedata.normalize("NFKC", text).casefold().strip()
    if not t or len(t) > MAX_SCALAR_CHARS:
        return None
    try:
        m = _ISO.fullmatch(t)
        if m is not None:
            y, mo, d, hh, mm, ss, frac, tz = m.groups()
            if hh is None:
                return date(int(y), int(mo), int(d))
            micro = int((frac or "0")[:6].ljust(6, "0"))
            zone = None
            if tz == "z":
                zone = timezone.utc
            elif tz:
                digits = tz[1:].replace(":", "")
                offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
                zone = timezone(offset if tz[0] == "+" else -offset)
            return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), micro, tzinfo=zone)
        t = _ABBREVIATION_DOT.sub("", _ORDINAL_SUFFIX.sub("", t)).replace(",", " ")
        t = " ".join(t.split())
        for pattern, kind in _DATE_FORMATS:
            m = pattern.fullmatch(t)
            if m is None:
                continue
            a, b, c = m.groups()
            if kind == "ymd":
                return date(int(a), int(b), int(c))
            if kind == "dby" and b in _MONTHS:
                return date(int(c), _MONTHS[b], int(a))
            if kind == "bdy" and a in _MONTHS:
                return date(int(c), _MONTHS[a], int(b))
            if kind == "dmy":
                return date(int(c), int(b), int(a))
            if kind == "slash":
                day, month = (a, b) if day_first else (b, a)
                return date(int(c), int(month), int(day))
    except (ValueError, OverflowError):  # 31 February, hour 25, an offset of a day or more
        return None
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Languages: (ISO 639-1 code, other ISO 639-2/3 codes, English and native names). Each maps to the 639-1 code.
_LANGUAGE_TABLE = (
    ("af", "afr", "afrikaans"),
    ("am", "amh", "amharic|አማርኛ"),
    ("ar", "ara arb", "arabic|العربية"),
    ("az", "aze azj", "azerbaijani|azeri|azərbaycan"),
    ("bg", "bul", "bulgarian|български"),
    ("bn", "ben", "bengali|bangla|বাংলা"),
    ("bs", "bos", "bosnian|bosanski"),
    ("ca", "cat", "catalan|català"),
    ("cs", "cze ces", "czech|čeština"),
    ("cy", "wel cym", "welsh|cymraeg"),
    ("da", "dan", "danish|dansk"),
    ("de", "ger deu", "german|deutsch"),
    ("el", "gre ell", "greek|ελληνικά"),
    ("en", "eng", "english"),
    ("eo", "epo", "esperanto"),
    ("es", "spa", "spanish|castilian|español|espanol|castellano"),
    ("et", "est ekk", "estonian|eesti"),
    ("eu", "baq eus", "basque|euskara"),
    ("fa", "per fas pes", "persian|farsi|فارسی"),
    ("fi", "fin", "finnish|suomi"),
    ("fr", "fre fra", "french|français|francais"),
    ("ga", "gle", "irish|gaeilge"),
    ("gl", "glg", "galician|galego"),
    ("gu", "guj", "gujarati|ગુજરાતી"),
    ("ha", "hau", "hausa"),
    ("he", "heb iw", "hebrew|עברית"),
    ("hi", "hin", "hindi|हिन्दी|हिंदी"),
    ("hr", "hrv", "croatian|hrvatski"),
    ("hu", "hun", "hungarian|magyar"),
    ("hy", "arm hye", "armenian|հայերեն"),
    ("id", "ind in", "indonesian|bahasa indonesia"),
    ("ig", "ibo", "igbo"),
    ("is", "ice isl", "icelandic|íslenska"),
    ("it", "ita", "italian|italiano"),
    ("ja", "jpn", "japanese|日本語"),
    ("ka", "geo kat", "georgian|ქართული"),
    ("kk", "kaz", "kazakh|қазақ тілі"),
    ("km", "khm", "khmer|ខ្មែរ"),
    ("kn", "kan", "kannada|ಕನ್ನಡ"),
    ("ko", "kor", "korean|한국어"),
    ("la", "lat", "latin|latina"),
    ("lo", "lao", "lao"),
    ("lt", "lit", "lithuanian|lietuvių"),
    ("lv", "lav lvs", "latvian|latviešu"),
    ("mk", "mac mkd", "macedonian|македонски"),
    ("ml", "mal", "malayalam|മലയാളം"),
    ("mn", "mon khk", "mongolian|монгол"),
    ("mr", "mar", "marathi|मराठी"),
    ("ms", "may msa zsm", "malay|bahasa melayu"),
    ("mt", "mlt", "maltese|malti"),
    ("my", "bur mya", "burmese|myanmar|မြန်မာ"),
    ("nb", "nob", "norwegian bokmål|bokmål|bokmal"),
    ("ne", "nep npi", "nepali|नेपाली"),
    ("nl", "dut nld", "dutch|flemish|nederlands"),
    ("nn", "nno", "norwegian nynorsk|nynorsk"),
    ("no", "nor", "norwegian|norsk"),
    ("pa", "pan", "punjabi|panjabi|ਪੰਜਾਬੀ"),
    ("pl", "pol", "polish|polski"),
    ("pt", "por", "portuguese|português|portugues"),
    ("ro", "rum ron", "romanian|română"),
    ("ru", "rus", "russian|русский"),
    ("si", "sin", "sinhala|sinhalese|සිංහල"),
    ("sk", "slo slk", "slovak|slovenčina"),
    ("sl", "slv", "slovenian|slovene|slovenščina"),
    ("sq", "alb sqi als", "albanian|shqip"),
    ("sr", "srp", "serbian|српски|srpski"),
    ("sv", "swe", "swedish|svenska"),
    ("sw", "swa swh", "swahili|kiswahili"),
    ("ta", "tam", "tamil|தமிழ்"),
    ("te", "tel", "telugu|తెలుగు"),
    ("th", "tha", "thai|ไทย"),
    ("tl", "tgl fil", "tagalog|filipino"),
    ("tr", "tur", "turkish|türkçe|turkce"),
    ("uk", "ukr", "ukrainian|українська"),
    ("ur", "urd", "urdu|اردو"),
    ("uz", "uzb uzn", "uzbek|oʻzbek|o'zbek"),
    ("vi", "vie", "vietnamese|tiếng việt"),
    ("xh", "xho", "xhosa|isixhosa"),
    ("yo", "yor", "yoruba|yorùbá"),
    ("zh", "chi zho cmn", "chinese|mandarin|中文|汉语|漢語|普通话"),
    ("zu", "zul", "zulu|isizulu"),
)


def _language_key(text: str) -> str:
    return " ".join(_rstrip(unicodedata.normalize("NFKC", text).casefold().strip(), _TRAILING).split())


LANGUAGE_CODES: dict[str, str] = {}  # every code (639-1, -2, -3) -> 639-1
LANGUAGE_NAMES: dict[str, str] = {}  # every English or native name -> 639-1
for _code, _others, _names in _LANGUAGE_TABLE:
    for _c in (_code, *_others.split()):
        LANGUAGE_CODES[_c] = _code
    for _n in _names.split("|"):
        LANGUAGE_NAMES[_language_key(_n)] = _code
# A locale tag, as BCP 47 writes it (or POSIX, with '_'): a language subtag, then an optional script ('Hant'), region
# ('US', '419') and variants ('valencia', '1996'): "en-US", "pt_BR", "zh-Hant-TW", "es-419". Only '-' and '_' separate
# subtags, and the whole value must be the tag, so prose is never one ("It is English" would otherwise be Italian).
_LOCALE = re.compile(r"([a-z]{2,3})(?:[-_][a-z]{4})?(?:[-_](?:[a-z]{2}|\d{3}))?(?:[-_](?:[a-z0-9]{5,8}|\d[a-z0-9]{3}))*")
# The 3-letter codes BCP 47 takes as a tag's language: individual languages of a macrolanguage in the table ('cmn-Hans',
# 'fil-PH'). A language with a 639-1 code is tagged with it, so a 639-2 code ('ger-DE', 'spa-ES') is no tag, and neither
# is a retired 639-1 code: 'in' and 'iw' would make 'in-depth' Indonesian.
_LOCALE_THREE = frozenset("als arb azj cmn ekk fil khk lvs npi pes swh uzn zsm".split())
_RETIRED = frozenset({"in", "iw"})
_PARENS = re.compile(r"\([^()]*\)")


def language_code(text: str) -> str | None:
    """The ISO 639-1 code of a language written as a 639-1/2/3 code, a locale tag ('en-US'), or its English or native
    name ('English', 'Deutsch'); None for one the table does not know, and for anything else, such as a sentence that
    starts with a code ('It is English', 'no idea')."""
    if len(text) > MAX_SCALAR_CHARS:
        return None
    t = _language_key(_PARENS.sub(" ", text))
    found = LANGUAGE_CODES.get(t) or LANGUAGE_NAMES.get(t)
    if found is None and (m := _LOCALE.fullmatch(t)) is not None:
        lang = m.group(1)
        if (len(lang) == 2 and lang not in _RETIRED) or lang in _LOCALE_THREE:
            found = LANGUAGE_CODES.get(lang)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Free text


_APOSTROPHES = frozenset("'‘’ʼ`´")


def loose_text(text: str) -> str:
    """Text as the 'loose' mode compares it: NFKC, case-folded, whitespace collapsed, and Unicode punctuation read as a
    space between words ('Acme,Inc.' and 'Acme, Inc.' are 'acme inc', 'hello-world' is 'hello world'), except
    apostrophes, which are dropped ("O'Brien" is 'obrien'), and the dots of initials ('U.S.A.' is 'usa', 'e.g.' is
    'eg'). Linear: one pass over the characters, one over the words."""
    t = unicodedata.normalize("NFKC", text).casefold()
    chars = []
    for ch in t:
        if ch == "." or not unicodedata.category(ch).startswith("P"):
            chars.append(ch)  # a dot is resolved per word below
        elif ch not in _APOSTROPHES:
            chars.append(" ")
    words: list[str] = []
    for chunk in "".join(chars).split():
        if "." not in chunk:
            words.append(chunk)
            continue
        parts = [p for p in chunk.split(".") if p]
        if len(parts) > 1 and all(len(p) == 1 and p.isalpha() for p in parts):
            words.append("".join(parts))  # initials: 'u.s.a.'
        else:
            words.extend(parts)  # 'inc.', 'acme.inc', '3.5'
    return " ".join(words)


class Record(dict):  # type: ignore[type-arg]
    """One normalised record (path -> value): a dict marked as Ratchet's own, so `jsonable` recurses into it but never
    into a raw JSON value an `exact` field kept (which can be nested as deep as the parser allowed)."""


def jsonable(value: Any) -> Any:
    """A normalised value as JSON can hold it (`Task.canonical`): sets as sorted lists, records and rankings as lists."""
    if isinstance(value, frozenset):
        items = [jsonable(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(value, tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Record):
        return {k: jsonable(v) for k, v in value.items()}
    return value


def _json_text(value: Any) -> str | None:
    try:
        return json.dumps(value, sort_keys=True)
    except (RecursionError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------------------------------
# The field spec


def _valid_path(path: str) -> bool:
    """'$' (the whole answer) or dot-separated keys into nested objects ('customer.address.city'); no empty key."""
    return path == "$" or (0 < len(path) <= 256 and all(path.split(".")))


_EVERY_TYPE = frozenset(TYPES) - RESERVED_TYPES
# The types that use each FieldSpec option. Any other type must leave the option at its default, so a spec never holds
# a setting that silently does nothing (`FieldSpec.__post_init__`; `taskspec.OPTIONS` reads this table too).
OPTION_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "required": _EVERY_TYPE,
        "nullable": _EVERY_TYPE,
        "values": frozenset({"label", "language", "set", "ranking"}),
        "aliases": frozenset({"label", "ordinal", "set", "ranking"}),
        "abs_tol": frozenset({"number"}),
        "rel_tol": frozenset({"number"}),
        "decimal": frozenset({"number"}),
        "percent": frozenset({"number"}),
        "order": frozenset({"ordinal"}),
        "tolerance": frozenset({"ordinal"}),
        "mode": frozenset({"text", "ranking"}),
        "strip_suffixes": frozenset({"text"}),
        "day_first": frozenset({"date"}),
        "granularity": frozenset({"date"}),
        "item": frozenset({"set", "ranking"}),
        "min_jaccard": frozenset({"set"}),
        "split": frozenset({"set"}),
        "empty_values": frozenset({"set"}),
        "fields": frozenset({"records"}),
        "key": frozenset({"records"}),
        "ordered": frozenset({"records"}),
        "k": frozenset({"ranking"}),
    }
)


def _default(f: Any) -> Any:
    """A dataclass field's default value."""
    return f.default_factory() if f.default is _NO_DEFAULT else f.default


@dataclass(frozen=True)
class FieldSpec:
    """One field of a task's answer that must agree, and how. `path` is '$' or a dot path into nested objects; `type`
    is one of `TYPES` (see the table in docs/CONFIGURATION.md#tasks). A `required` field must be present, and null only
    when `nullable`; an optional field that is missing or null is None, so two answers without it agree. The options
    after `nullable` belong to the types named in their comments. Invalid options raise ValueError here, so a bad spec
    fails when the task is defined, not on the request path."""

    path: str
    type: str = "exact"
    required: bool = True
    nullable: bool = False
    values: tuple[str, ...] = ()  # label, language; set and ranking with the default item: the allowed values
    aliases: Mapping[str, str] = field(default_factory=dict)  # label, ordinal, set, ranking: other spellings
    abs_tol: float = 0.0  # number
    rel_tol: float = 0.0  # number
    decimal: str = "."  # number: the decimal mark, '.' or ','
    percent: str = "number"  # number: '12%' is 12 ('number') or 0.12 ('fraction')
    order: tuple[str, ...] = ()  # ordinal: the labels, lowest first
    tolerance: int = 0  # ordinal: how many steps apart two answers may be
    mode: str = "loose"  # text: 'exact', 'loose' or 'token_set'; ranking: 'prefix' (the default) or 'set'
    strip_suffixes: tuple[str, ...] = ()  # text (loose, token_set): trailing tokens dropped ('inc', 'ltd', 'gmbh')
    day_first: bool = False  # date: '03/10/2026' is 3 October
    granularity: str = "date"  # date: 'date' or 'datetime' (normalised to UTC)
    item: FieldSpec | str | None = None  # set, ranking: how each element is normalised (default: label)
    min_jaccard: float = 1.0  # set: the overlap at which two sets agree
    split: str | None = None  # set: a string is split on this (otherwise a set must be a list)
    empty_values: tuple[Any, ...] = ()  # set: elements dropped before comparing ('none', 'n/a')
    fields: tuple[FieldSpec, ...] = ()  # records: the fields of each record
    key: tuple[str, ...] = ()  # records: the fields that pair records (else in any order, or by position if `ordered`)
    ordered: bool = False  # records: pair by position
    k: int = 0  # ranking: how many leading ids are compared (0: all)

    # Derived in __post_init__.
    _segments: tuple[str, ...] = field(init=False, repr=False, compare=False, default=())
    _allowed: frozenset[str] | None = field(init=False, repr=False, compare=False, default=None)
    _alias_map: Mapping[str, str] = field(init=False, repr=False, compare=False, default_factory=dict)
    _index: Mapping[str, int] = field(init=False, repr=False, compare=False, default_factory=dict)
    _suffixes: Mapping[str, tuple[tuple[str, ...], ...]] = field(init=False, repr=False, compare=False, default_factory=dict)
    _item_spec: FieldSpec | None = field(init=False, repr=False, compare=False, default=None)
    _empty: frozenset[Any] = field(init=False, repr=False, compare=False, default=frozenset())
    # Two values match exactly when their `_token`s are equal (no tolerance anywhere inside), so records can be grouped
    # by such fields in linear time. For records: the sub-fields that do, and the ones that do not.
    _by_equality: bool = field(init=False, repr=False, compare=False, default=True)
    _eq_fields: tuple[FieldSpec, ...] = field(init=False, repr=False, compare=False, default=())
    _tol_fields: tuple[FieldSpec, ...] = field(init=False, repr=False, compare=False, default=())
    depth: int = field(init=False, repr=False, compare=False, default=0)  # records nesting: 0 for other types
    size: int = field(init=False, repr=False, compare=False, default=1)  # this field and every field inside it

    def _fail(self, message: str) -> None:
        raise ValueError(f"field {self.path!r} ({self.type}): {message}")

    def _set(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not _valid_path(self.path):
            raise ValueError(f"field path {self.path!r}: use '$' or dot-separated keys")
        if self.type in RESERVED_TYPES:
            self._fail("this type is reserved for a later release")
        if self.type not in _NORMALIZE:
            self._fail(f"unknown type; use one of {', '.join(t for t in TYPES if t not in RESERVED_TYPES)}")
        for name in ("required", "nullable", "day_first", "ordered"):
            if not isinstance(getattr(self, name), bool):
                self._fail(f"{name} must be true or false")
        for name in ("values", "order", "strip_suffixes", "empty_values", "fields", "key"):
            v = getattr(self, name)
            if isinstance(v, (str, bytes)) or not isinstance(v, (tuple, list)):
                self._fail(f"{name} must be a list")
            self._set(name, tuple(v))
            if len(v) > MAX_VALUES:
                self._fail(f"{name} has more than {MAX_VALUES} entries")
        if not isinstance(self.aliases, Mapping):
            self._fail("aliases must be a mapping")
        if len(self.aliases) > MAX_VALUES:
            self._fail(f"aliases has more than {MAX_VALUES} entries")
        if not all(isinstance(a, str) and isinstance(b, str) for a, b in self.aliases.items()):
            self._fail("aliases map strings to strings")
        self._set("aliases", MappingProxyType(dict(self.aliases)))  # read-only: the spec is immutable
        for name in ("values", "order", "strip_suffixes"):
            if not all(isinstance(v, str) for v in getattr(self, name)):
                self._fail(f"{name} must hold strings")
        for name in ("abs_tol", "rel_tol", "min_jaccard"):
            v = getattr(self, name)
            if not _is_number(v) or not math.isfinite(v) or v < 0:
                self._fail(f"{name} must be a finite number >= 0")
        if self.min_jaccard > 1:
            self._fail("min_jaccard must be between 0 and 1")
        for name in ("tolerance", "k"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                self._fail(f"{name} must be an integer >= 0")
        if self.decimal not in (".", ","):
            self._fail("decimal must be '.' or ','")
        if self.percent not in ("number", "fraction"):
            self._fail("percent must be 'number' or 'fraction'")
        if self.granularity not in ("date", "datetime"):
            self._fail("granularity must be 'date' or 'datetime'")
        if self.split is not None and (not isinstance(self.split, str) or not self.split):
            self._fail("split must be a non-empty string")
        self._set("_segments", () if self.path == "$" else tuple(self.path.split(".")))

        t = self.type
        for f in fields(self):
            if f.init and f.name in OPTION_TYPES and t not in OPTION_TYPES[f.name] and getattr(self, f.name) != _default(f):
                self._fail(f"{f.name} does not apply to this type")

        if t == "text" and self.mode not in ("exact", "loose", "token_set"):
            self._fail("mode must be 'exact', 'loose' or 'token_set'")
        if t == "ranking":
            if self.mode == "loose":  # the shared default: a ranking compares its prefix unless told otherwise
                self._set("mode", "prefix")
            if self.mode not in ("prefix", "set"):
                self._fail("mode must be 'prefix' or 'set'")
        if t in ("label", "ordinal"):
            self._labels()
        elif t == "language":
            codes = []
            for v in self.values:
                code = language_code(v)
                if code is None:
                    self._fail(f"unknown language {v!r} in values")
                codes.append(code)
            self._set("_allowed", frozenset(codes) if codes else None)
        elif t == "text":
            index: dict[str, list[tuple[str, ...]]] = {}
            for s in self.strip_suffixes:
                tokens = tuple(loose_text(s).split())
                if not tokens:
                    self._fail(f"suffix {s!r} is empty once punctuation is dropped")
                index.setdefault(tokens[-1], []).append(tokens)
            self._set("_suffixes", {last: tuple(sorted(v, key=len, reverse=True)) for last, v in index.items()})
        elif t in ("set", "ranking"):
            self._items()
        elif t == "records":
            self._records()
        if t == "number":
            self._set("_by_equality", not self.abs_tol and not self.rel_tol)
        elif t == "ordinal":
            self._set("_by_equality", not self.tolerance)
        elif t == "set":
            self._set("_by_equality", self.min_jaccard >= 1)
        elif t == "records":
            self._set("_eq_fields", tuple(f for f in self.fields if f._by_equality))
            self._set("_tol_fields", tuple(f for f in self.fields if not f._by_equality))
            self._set("_by_equality", not self._tol_fields)

    def _labels(self) -> None:
        """label and ordinal: the normalised vocabulary (`values`, or `order` with each label's position) and aliases.
        With a vocabulary, an alias must name one of its labels and must not be one itself (unless it names itself):
        `{"high": "urgent"}` would silently stop 'high' from meaning high."""
        vocab = self.order if self.type == "ordinal" else self.values
        if self.type == "ordinal" and not vocab:
            self._fail("order must list the labels, lowest first")
        normal = [label_text(v) for v in vocab]
        if len(set(normal)) != len(normal):
            self._fail("two entries are the same label once normalised")
        allowed = frozenset(normal) if normal else None
        aliases: dict[str, str] = {}
        for a, b in self.aliases.items():
            ka, kb = label_text(a), label_text(b)
            if allowed is not None and kb not in allowed:
                self._fail("an alias names a label outside the vocabulary")
            if allowed is not None and ka in allowed and ka != kb:
                self._fail(f"alias {a!r} is a label of the vocabulary itself")
            if aliases.get(ka, kb) != kb:
                self._fail(f"alias {a!r} names two labels once normalised")
            aliases[ka] = kb
        self._set("_allowed", allowed)
        self._set("_alias_map", aliases)
        if self.type == "ordinal":
            self._set("_index", {v: i for i, v in enumerate(normal)})

    def _items(self) -> None:
        """set and ranking: the element spec (a label over `values` and `aliases` unless `item` says otherwise)."""
        item = self.item
        if item is None:
            item = FieldSpec("$", "label", values=self.values, aliases=self.aliases)
        else:
            if self.values or self.aliases:
                self._fail("with an item spec, put values and aliases on the item")
            if isinstance(item, str):
                item = FieldSpec("$", item)
            elif not isinstance(item, FieldSpec):
                self._fail("item must be a FieldSpec or a type name")
            self._set("item", item)
        if item.type not in SCALAR_TYPES:
            self._fail(f"elements must be of a scalar type ({', '.join(sorted(SCALAR_TYPES))})")
        if item.abs_tol or item.rel_tol or item.tolerance:
            self._fail("elements compare exactly: use records for elements with a tolerance")
        self._set("_item_spec", item)
        empty = set()
        for v in self.empty_values:
            token = _empty_token(v)
            if token is None:
                self._fail("empty_values must be strings, numbers, booleans or null")
            empty.add(token)
        self._set("_empty", frozenset(empty))

    def _records(self) -> None:
        if not self.fields:
            self._fail("fields must list each record's fields")
        if not all(isinstance(f, FieldSpec) for f in self.fields):
            self._fail("fields must be FieldSpecs")
        paths = [f.path for f in self.fields]
        if "$" in paths:
            self._fail("a record field needs a key path, not '$'")
        if len(set(paths)) != len(paths):
            self._fail("two record fields have the same path")
        by_path = {f.path: f for f in self.fields}
        for p in self.key:
            f = by_path.get(p)
            if f is None:
                self._fail(f"key {p!r} is not one of the record's fields")
            if f.type not in SCALAR_TYPES or f.abs_tol or f.rel_tol or f.tolerance:
                self._fail(f"key {p!r} must be a scalar field without a tolerance")
        if len(set(self.key)) != len(self.key):
            self._fail("key names a field twice")
        depth = 1 + max(f.depth for f in self.fields)
        if depth > MAX_DEPTH:
            self._fail(f"records are nested more than {MAX_DEPTH} deep")
        self._set("depth", depth)
        self._set("size", 1 + sum(f.size for f in self.fields))
        if self.size > MAX_FIELDS:
            self._fail(f"more than {MAX_FIELDS} fields, counting the record's own")

    # -----------------------------------------------------------------------------------------------------------------
    def resolve(self, root: Any) -> Any:
        """The raw value at this field's path in `root`, or MISSING."""
        cur = root
        for seg in self._segments:
            if not isinstance(cur, dict) or seg not in cur:
                return MISSING
            cur = cur[seg]
        return cur

    def normalize(self, raw: Any, budget: list[int] | None = None) -> Any:
        """The normalised value of `raw` (MISSING when the path is absent), or INVALID. A missing or null optional field
        is None; a required one is INVALID, unless it is null and `nullable`. `budget` is the records an answer may
        still hold (`MAX_RECORDS`), shared by every field of one answer."""
        if raw is MISSING or raw is None:
            if self.required and (raw is MISSING or not self.nullable):
                return INVALID
            return None
        return _NORMALIZE[self.type](self, raw, budget if budget is not None else [MAX_RECORDS])

    def match(self, a: Any, b: Any) -> bool:
        """Whether two normalised values agree. None (an absent optional field, or null) agrees only with None."""
        if a is INVALID or b is INVALID:
            return False
        if a is None or b is None:
            return a is None and b is None
        return _MATCH[self.type](self, a, b)

    def spec(self) -> dict[str, Any]:
        """This field as a plain dict: `path`, `type` and every option that is not its default (for the task's
        `spec_hash` and for listing a task)."""
        out: dict[str, Any] = {"path": self.path, "type": self.type}
        for f in fields(self):
            if not f.init or f.name in ("path", "type"):
                continue
            v = getattr(self, f.name)
            if v == _default(f):
                continue
            if isinstance(v, FieldSpec):
                v = v.spec()
            elif f.name == "fields":
                v = [x.spec() for x in v]
            elif isinstance(v, Mapping):
                v = dict(sorted(v.items()))
            elif isinstance(v, tuple):
                v = list(v)
            out[f.name] = v
        return out


def _empty_token(v: Any) -> tuple[str, Any] | None:
    """How a set element is matched against `empty_values` before it is normalised: strings stripped and case-folded,
    other scalars by type and value (so 0 never matches False)."""
    if isinstance(v, str):
        return ("str", v.strip().casefold())
    if v is None or isinstance(v, (bool, int, float)):
        return (type(v).__name__, v)
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Normalisers: (spec, raw, budget) -> value | INVALID. `raw` is never None or MISSING here.


def _n_exact(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    """The v0.4 rule: strings stripped and lower-cased, anything else as it is (compared as JSON text)."""
    if isinstance(raw, str):
        return raw.strip().lower()
    if not isinstance(raw, (bool, int, float)) and _json_text(raw) is None:
        return INVALID  # nested too deeply to compare
    return raw


def _n_label(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return INVALID
    v = label_text(str(raw))
    v = spec._alias_map.get(v, v)
    if spec._allowed is not None and v not in spec._allowed:
        return INVALID
    return v


_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})


def _n_bool(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if isinstance(raw, bool):
        return raw
    if _is_number(raw):
        return True if raw == 1 else False if raw == 0 else INVALID
    if isinstance(raw, str) and len(raw) <= MAX_SCALAR_CHARS:
        t = label_text(raw)
        return True if t in _TRUE else False if t in _FALSE else INVALID
    return INVALID


def _n_number(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if isinstance(raw, bool):
        return INVALID
    if isinstance(raw, int):
        try:
            return float(raw)
        except OverflowError:
            return INVALID
    if isinstance(raw, float):
        return raw if math.isfinite(raw) else INVALID
    if isinstance(raw, str):
        return parse_number(raw, spec.decimal, spec.percent)
    return INVALID


def _n_ordinal(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    v = _n_label(spec, raw, budget)
    return INVALID if v is INVALID else spec._index[v]


def _n_text(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):  # a float's '.' would be dropped as punctuation
        return INVALID
    s = raw if isinstance(raw, str) else str(raw)
    if spec.mode == "exact":
        return " ".join(s.casefold().split())
    tokens = loose_text(s).split()
    dropped = True
    while dropped and spec._suffixes and len(tokens) > 1:  # each pass drops at least one token or stops
        dropped = False
        for suffix in spec._suffixes.get(tokens[-1], ()):
            n = len(suffix)
            if len(tokens) > n and tuple(tokens[-n:]) == suffix:
                del tokens[-n:]
                dropped = True
                break
    return frozenset(tokens) if spec.mode == "token_set" else " ".join(tokens)


def _n_date(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if not isinstance(raw, str):
        return INVALID
    d = parse_date(raw, spec.day_first)
    if d is None:
        return INVALID
    if spec.granularity == "date":
        return (d.date() if isinstance(d, datetime) else d).isoformat()  # the day as written, in its own time zone
    if not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    elif d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    try:
        return d.astimezone(timezone.utc).isoformat()
    except OverflowError:  # year 1 or 9999 shifted past the calendar
        return INVALID


def _n_language(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if not isinstance(raw, str):
        return INVALID
    code = language_code(raw)
    if code is None or (spec._allowed is not None and code not in spec._allowed):
        return INVALID
    return code


def _elements(spec: FieldSpec, raw: Any) -> list[Any] | Any:
    if isinstance(raw, str) and spec.type == "set":
        if spec.split is None:
            return INVALID
        raw = raw.split(spec.split)
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        return INVALID
    return raw


def _n_set(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    items = _elements(spec, raw)
    if items is INVALID:
        return INVALID
    item = spec._item_spec
    assert item is not None
    out = set()
    for el in items:
        token = _empty_token(el)
        if token is not None and token in spec._empty:
            continue
        v = item.normalize(el.strip() if isinstance(el, str) else el, budget)
        if v is INVALID or not _hashable(v):
            return INVALID
        out.add(v)
    return frozenset(out)


def _n_ranking(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    items = _elements(spec, raw)
    if items is INVALID:
        return INVALID
    item = spec._item_spec
    assert item is not None
    out = []
    for el in items:
        v = item.normalize(el, budget)
        if v is INVALID or not _hashable(v):
            return INVALID
        out.append(v)
    if len(set(out)) != len(out):  # an id ranked twice: not a ranking
        return INVALID
    return tuple(out)


def _n_records(spec: FieldSpec, raw: Any, budget: list[int]) -> Any:
    if not isinstance(raw, list) or len(raw) > MAX_RECORDS:
        return INVALID
    budget[0] -= len(raw)
    if budget[0] < 0:
        return INVALID
    out = []
    for el in raw:
        if not isinstance(el, dict):
            return INVALID
        rec = Record()
        for f in spec.fields:
            v = f.normalize(f.resolve(el), budget)
            if v is INVALID:
                return INVALID
            rec[f.path] = v
        out.append(rec)
    if spec.key:
        keys = [tuple(r[p] for p in spec.key) for r in out]
        if not all(_hashable(k) for k in keys) or len(set(keys)) != len(keys):
            return INVALID  # two records with one key cannot be paired
    return tuple(out)


_NORMALIZE: dict[str, Callable[[FieldSpec, Any, list[int]], Any]] = {
    "exact": _n_exact,
    "label": _n_label,
    "bool": _n_bool,
    "number": _n_number,
    "ordinal": _n_ordinal,
    "text": _n_text,
    "date": _n_date,
    "language": _n_language,
    "set": _n_set,
    "records": _n_records,
    "ranking": _n_ranking,
}


# ---------------------------------------------------------------------------------------------------------------------
# Matchers: (spec, a, b) -> bool on two normalised values, neither None.


def _m_equal(spec: FieldSpec, a: Any, b: Any) -> bool:
    return bool(a == b)


def _m_exact(spec: FieldSpec, a: Any, b: Any) -> bool:
    """Equal as JSON text, as v0.4's canonical JSON compared them: 1 and 1.0 differ, and so do true and 1. A value always
    matches itself, even one that `json.dumps` cannot write from a deeper stack than it was normalised on."""
    if a is b:
        return True
    if type(a) is type(b) and isinstance(a, str):
        return a == b
    ja = _json_text(a)
    return ja is not None and ja == _json_text(b)


def _m_number(spec: FieldSpec, a: float, b: float) -> bool:
    return a == b or abs(a - b) <= max(spec.abs_tol, spec.rel_tol * max(abs(a), abs(b)))


def _m_ordinal(spec: FieldSpec, a: int, b: int) -> bool:
    return abs(a - b) <= spec.tolerance


def _m_set(spec: FieldSpec, a: frozenset[Any], b: frozenset[Any]) -> bool:
    if not a and not b:
        return True
    shared = len(a & b)
    return shared >= spec.min_jaccard * (len(a) + len(b) - shared) - 1e-9


def _token(spec: FieldSpec, v: Any) -> Any:
    """A hashable stand-in for a normalised value: for a field that compares by equality (`FieldSpec._by_equality`), two
    values match exactly when their tokens are equal; for any field, equal tokens mean equal values, which match because
    every value matches itself. None (an absent optional field) is its own token."""
    t = spec.type
    if v is None:
        return None
    if t == "exact":
        if isinstance(v, str):
            return ("s", v)
        text = _json_text(v)
        return ("j", text) if text is not None else object()  # cannot be written: equal to nothing, as in _m_exact
    if t == "ranking":
        top = v[: spec.k] if spec.k else v
        return top if spec.mode == "prefix" else frozenset(top)
    if t == "records":
        tokens = [tuple(_token(f, r[f.path]) for f in spec.fields) for r in v]
        # Keyed records have unique keys, so for them too a match is the same multiset of records.
        return ("o", tuple(tokens)) if spec.ordered else ("u", frozenset(Counter(tokens).items()))
    return v  # label, bool, number and ordinal without a tolerance, text, date, language, set with min_jaccard 1


def _perfect_matching(xs: list[Record], ys: list[Record], ok: Callable[[Record, Record], bool]) -> bool:
    """Whether every record of `xs` can take its own partner in `ys` (equal lengths) under `ok`: a maximum bipartite
    matching, so the answer never depends on the order the records are listed in (a tolerance makes a first-fit pairing
    miss one: 1 taking 0.5 leaves 0 nothing, where 0 with 0.5 and 1 with 2 pair everything). A first-fit pass pairs
    the usual case; each record it leaves over gets an augmenting path (Kuhn's algorithm, iterative, so nested records
    never deepen the stack), and the first that has none settles it. At most len² calls of `ok`."""
    n = len(xs)
    owner = [-1] * n  # owner[j]: the record of xs paired with ys[j]
    left = []
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            if owner[j] < 0 and ok(x, y):
                owner[j] = i
                break
        else:
            left.append(i)
    rows: dict[int, list[int]] = {}

    def adjacent(i: int) -> list[int]:
        row = rows.get(i)
        if row is None:
            row = rows[i] = [j for j, y in enumerate(ys) if ok(xs[i], y)]
        return row

    for root in left:
        seen = [False] * n
        stack = [(root, 0)]  # (record of xs, where its scan of adjacent(record) resumes)
        path: list[int] = []  # path[d]: the ys index taken at depth d
        done = False
        while stack and not done:
            i, start = stack[-1]
            row = adjacent(i)
            for pos in range(start, len(row)):
                j = row[pos]
                if seen[j]:
                    continue
                seen[j] = True
                stack[-1] = (i, pos + 1)
                path.append(j)
                if owner[j] < 0:  # a free partner: shift every record on the path to the partner it reached
                    for (ii, _), jj in zip(stack, path, strict=True):
                        owner[jj] = ii
                    done = True
                else:
                    stack.append((owner[j], 0))
                break
            else:
                stack.pop()
                if path:
                    path.pop()
        if not done:
            return False
    return True


def _m_records(spec: FieldSpec, a: tuple[Record, ...], b: tuple[Record, ...]) -> bool:
    if len(a) != len(b):
        return False
    subs = spec.fields

    def same(x: Record, y: Record) -> bool:
        return all(f.match(x[f.path], y[f.path]) for f in subs)

    if spec.ordered:
        return all(same(x, y) for x, y in zip(a, b, strict=True))
    if spec.key:
        by_key = {tuple(y[p] for p in spec.key): y for y in b}
        for x in a:
            y = by_key.get(tuple(x[p] for p in spec.key))
            if y is None or not same(x, y):
                return False
        return True
    # Unordered without a key. Two records can pair only when their fields without a tolerance are equal, so group the
    # records by those fields' tokens (linear), then match the fields with a tolerance inside each group.
    groups: dict[tuple[Any, ...], tuple[list[Record], list[Record]]] = {}
    for x in a:
        groups.setdefault(tuple(_token(f, x[f.path]) for f in spec._eq_fields), ([], []))[0].append(x)
    for y in b:
        group = groups.get(tuple(_token(f, y[f.path]) for f in spec._eq_fields))
        if group is None:
            return False
        group[1].append(y)
    loose = spec._tol_fields

    def close(x: Record, y: Record) -> bool:
        return all(f.match(x[f.path], y[f.path]) for f in loose)

    for xs, ys in groups.values():
        if len(xs) != len(ys):
            return False
        if not loose or _same_values(loose, xs, ys):  # the same records in another order pair with themselves
            continue
        if not _perfect_matching(xs, ys, close):
            return False
    return True


def _same_values(subs: tuple[FieldSpec, ...], xs: list[Record], ys: list[Record]) -> bool:
    """Whether `xs` and `ys` hold the same values of `subs`, as multisets (linear). Then each record can pair with its
    equal, because every value matches itself."""
    return Counter(tuple(_token(f, x[f.path]) for f in subs) for x in xs) == Counter(
        tuple(_token(f, y[f.path]) for f in subs) for y in ys
    )


def _m_ranking(spec: FieldSpec, a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    top_a, top_b = (a[: spec.k], b[: spec.k]) if spec.k else (a, b)
    return top_a == top_b if spec.mode == "prefix" else set(top_a) == set(top_b)


_MATCH: dict[str, Callable[[FieldSpec, Any, Any], bool]] = {
    "exact": _m_exact,
    "label": _m_equal,
    "bool": _m_equal,
    "number": _m_number,
    "ordinal": _m_ordinal,
    "text": _m_equal,
    "date": _m_equal,
    "language": _m_equal,
    "set": _m_set,
    "records": _m_records,
    "ranking": _m_ranking,
}
