"""Free-text similarity for the `similarity` field type (FR-2.55, ADR 0054).

Two answers to a summary or a short translation are rarely equal, so the exact comparators call every pair of good
answers a disagreement and the student never earns a confident score. These metrics give a score from 0 to 1 instead;
`compare.FieldSpec` calls two answers agreeing when the score reaches the field's `threshold` and their lengths are
within `max_len_ratio` of each other. Agreement under them is a proxy: close to the teacher's answer, not checked
correct, which is why the golden eval labels it `precision_kind: proxy` and can ask the judge as well.

- `chrf`: character n-gram F-score (Popovic 2015, sacrebleu's chrF): n-grams of 1 to 6 characters of the text with its
  whitespace removed, precision and recall averaged over the orders, then F-beta with beta 2 (recall weighs four times
  as much). An order only one text is long enough for counts as no match. No tokenizer, so it suits any script.
- `token_f1`: SQuAD's token F1: punctuation and symbols deleted, the English articles a, an and the dropped, whitespace
  tokens, F1 of the overlap of the two bags of tokens.
- `rouge_l`: ROUGE-L F1 over the longest common subsequence of word tokens (runs of letters and digits), each answer
  capped at its first `MAX_LCS_TOKENS` tokens.

Text is NFKC-normalised and case-folded first (`normal`). Every score is symmetric, because the confidence score compares
each pair of samples once (`confidence.score`): chrF with beta 2 is not, so `score` takes the lower of its two
directions. Standard library only, no I/O. A sample is compared with every other one on the request path, so each text's
features are computed once (`features`) and every comparison is linear in them; ROUGE-L uses a bit-parallel LCS
(Hyyro 2004), a few hundred integer operations for 512 tokens. Token metrics need words separated by spaces; use `chrf`
for Chinese, Japanese or Thai.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Any

METRICS = ("chrf", "token_f1", "rouge_l")
CHRF_ORDER = 6  # character n-grams of 1 to 6
CHRF_BETA = 2.0  # recall weighs beta^2 = 4 times as much as precision
MAX_CHRF_CHARS = 8192  # characters (whitespace removed) chrF reads of one answer
MAX_F1_TOKENS = 4096  # tokens token F1 reads of one answer
MAX_LCS_TOKENS = 512  # tokens ROUGE-L reads of one answer
_ARTICLES = frozenset({"a", "an", "the"})
_WORD = re.compile(r"[^\W_]+")  # a run of letters and digits (any script)


def normal(text: str) -> str:
    """Text as every metric reads it: NFKC, case-folded, runs of whitespace collapsed to one space, stripped."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _squad_tokens(text: str) -> list[str]:
    """SQuAD's answer normalisation on case-folded text: punctuation and symbols deleted (so "don't" is "dont"), the
    English articles dropped, whitespace tokens."""
    kept = "".join(ch for ch in text if not unicodedata.category(ch).startswith(("P", "S")))
    return [w for w in kept.split() if w not in _ARTICLES]


def features(metric: str, text: str) -> Any:
    """What `metric` compares of one normalised text (`normal`), computed once per answer: for chrF the n-gram counts
    and their totals per order, for token F1 the bag of tokens, for ROUGE-L the token list. ValueError for an unknown
    metric."""
    if metric == "chrf":
        chars = "".join(text.split())[:MAX_CHRF_CHARS]
        grams = []
        for n in range(1, CHRF_ORDER + 1):
            counts = Counter(chars[i : i + n] for i in range(len(chars) - n + 1))
            grams.append((counts, max(0, len(chars) - n + 1)))
        return text, tuple(grams)
    if metric == "token_f1":
        tokens = _squad_tokens(text)[:MAX_F1_TOKENS]
        return text, Counter(tokens), len(tokens)
    if metric == "rouge_l":
        return text, tuple(_WORD.findall(text)[:MAX_LCS_TOKENS])
    raise ValueError(f"unknown similarity metric {metric!r}; use one of {', '.join(METRICS)}")


def _f_beta(p: float, r: float, beta: float) -> float:
    b2 = beta * beta
    denominator = b2 * p + r
    return (1 + b2) * p * r / denominator if denominator > 0 else 0.0


def _chrf(fa: tuple[Any, ...], fb: tuple[Any, ...]) -> float:
    precisions: list[float] = []
    recalls: list[float] = []
    for (ca, ta), (cb, tb) in zip(fa, fb, strict=True):
        if not ta and not tb:  # both texts shorter than n: the order says nothing
            continue
        if not ta or not tb:  # only one text has n-grams of this order: none of them can match
            precisions.append(0.0)
            recalls.append(0.0)
            continue
        small, big = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
        matched = sum(min(count, big.get(gram, 0)) for gram, count in small.items())
        precisions.append(matched / ta)
        recalls.append(matched / tb)
    if not precisions:
        return 0.0
    p, r = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    return min(_f_beta(p, r, CHRF_BETA), _f_beta(r, p, CHRF_BETA))  # the lower direction: symmetric


def lcs_length(a: Sequence[Any], b: Sequence[Any]) -> int:
    """The length of the longest common subsequence of two sequences of hashable items, bit-parallel (Hyyro 2004): one
    pass over `b`, each step a few operations on an integer of len(a) bits."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return 0
    masks: dict[Any, int] = {}
    for i, x in enumerate(a):
        masks[x] = masks.get(x, 0) | (1 << i)
    full = (1 << len(a)) - 1
    v = full
    for y in b:
        m = masks.get(y, 0)
        if m:
            u = v & m
            v = ((v + u) | (v - u)) & full
    return len(a) - v.bit_count()


def compare_features(metric: str, fa: Any, fb: Any) -> float:
    """The similarity of two texts from their `features` (the same metric's), from 0 to 1; 1 for equal texts."""
    if fa[0] == fb[0]:
        return 1.0
    if metric == "chrf":
        return _chrf(fa[1], fb[1])
    if metric == "token_f1":
        _, ca, na = fa
        _, cb, nb = fb
        if not na or not nb:  # no token left in one of them (only punctuation or articles): their texts differ
            return 0.0
        common = sum((ca & cb).values())
        return 0.0 if common == 0 else 2 * common / (na + nb)
    if metric == "rouge_l":
        ta, tb = fa[1], fb[1]
        if not ta or not tb:
            return 0.0
        return 2 * lcs_length(ta, tb) / (len(ta) + len(tb))
    raise ValueError(f"unknown similarity metric {metric!r}; use one of {', '.join(METRICS)}")


def score(a: str, b: str, metric: str = "chrf") -> float:
    """The similarity of two raw texts under `metric` (normalised first), from 0 to 1. Symmetric: score(a, b) equals
    score(b, a)."""
    na, nb = normal(a), normal(b)
    return compare_features(metric, features(metric, na), features(metric, nb))
