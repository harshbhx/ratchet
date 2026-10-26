"""Mock research world for Scout, the multi-agent demo (docs/SCOUT.md).

Eight questions about well-established ML ideas, with facts taken from the original papers.
Each question has a pool of simulated search results built from 12 templates. Every template
has fixed labels for the two distillable tasks (`source-triage`, `page-read`), so the mock
teacher is always right and the mock student can learn template by template, as with tickets.

URLs use example.org: nothing here pretends to be a real page.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any

from ratchet.scout.prompts import FACT_TYPES, SOURCE_TYPES, read_messages, triage_messages


@dataclass(frozen=True)
class Question:
    key: str
    question: str
    topic: str
    definition: str
    date: str
    extra: str
    extra_type: str  # "number" | "name"
    queries: tuple[str, str, str]


QUESTIONS: list[Question] = [
    Question(
        "lora",
        "What is LoRA fine-tuning?",
        "LoRA (low-rank adaptation)",
        "LoRA freezes the pretrained weights and trains small low-rank matrices added to each layer.",
        "LoRA was introduced by Hu et al. at Microsoft in 2021.",
        "On GPT-3 175B, LoRA reduced the number of trainable parameters by about 10,000 times.",
        "number",
        ("LoRA low-rank adaptation explained", "LoRA Hu et al. 2021 paper", "LoRA vs full fine-tuning trainable parameters"),
    ),
    Question(
        "distill",
        "What is knowledge distillation?",
        "knowledge distillation",
        "Knowledge distillation trains a small student model to match the soft output probabilities of a larger teacher model.",
        "The method was popularised by Hinton, Vinyals and Dean in 2015.",
        "The 2015 paper is titled 'Distilling the Knowledge in a Neural Network'.",
        "name",
        (
            "knowledge distillation explained",
            "Hinton distilling the knowledge in a neural network",
            "teacher student model soft targets",
        ),
    ),
    Question(
        "cascade",
        "How does a model cascade cut LLM costs?",
        "LLM cascades",
        "A cascade sends each query to a cheap model first and calls a more expensive model only when the answer is not reliable enough.",
        "FrugalGPT, published in 2023 by Chen, Zaharia and Zou, studied LLM cascades.",
        "FrugalGPT reported matching GPT-4 performance with up to 98% lower cost.",
        "number",
        ("LLM cascade cost reduction", "FrugalGPT paper 2023", "route queries cheap model then expensive model"),
    ),
    Question(
        "wilson",
        "What is the Wilson score interval?",
        "the Wilson score interval",
        "The Wilson score interval is a confidence interval for a binomial proportion that stays accurate for small samples and proportions near 0 or 1.",
        "Edwin Bidwell Wilson published the interval in 1927.",
        "It is named after the American mathematician Edwin Bidwell Wilson.",
        "name",
        (
            "Wilson score interval explained",
            "Wilson 1927 binomial confidence interval",
            "Wilson interval vs normal approximation",
        ),
    ),
    Question(
        "paged",
        "What is PagedAttention in vLLM?",
        "PagedAttention",
        "PagedAttention stores the attention key-value cache in fixed-size blocks, like pages in virtual memory, so little memory is wasted.",
        "PagedAttention was presented by Kwon et al. at SOSP 2023.",
        "The vLLM paper reported 2 to 4 times higher throughput than earlier serving systems at the same latency.",
        "number",
        ("vLLM PagedAttention explained", "Kwon et al. SOSP 2023 vLLM paper", "PagedAttention KV cache memory throughput"),
    ),
    Question(
        "mamba",
        "What is a Mamba state-space model?",
        "Mamba",
        "Mamba is a sequence model built on selective state spaces, so it scales linearly with sequence length instead of quadratically like attention.",
        "Mamba was introduced by Albert Gu and Tri Dao in December 2023.",
        "The Mamba paper reports about 5 times higher inference throughput than Transformers.",
        "number",
        ("Mamba selective state space model", "Gu and Dao 2023 Mamba paper", "Mamba vs Transformer throughput"),
    ),
    Question(
        "selfcons",
        "What is self-consistency decoding?",
        "self-consistency decoding",
        "Self-consistency samples several reasoning paths from a model and returns the answer most of them agree on.",
        "Self-consistency was proposed by Wang et al. at Google in 2022.",
        "On the GSM8K benchmark it raised chain-of-thought accuracy by 17.9 percentage points.",
        "number",
        ("self-consistency chain of thought", "Wang et al. 2022 self-consistency paper", "self-consistency GSM8K improvement"),
    ),
    Question(
        "collapse",
        "What is model collapse?",
        "model collapse",
        "Model collapse is the loss of quality and diversity that happens when models are trained on data generated by earlier models.",
        "Shumailov et al. described model collapse in 2023, and a version appeared in Nature in 2024.",
        "The 2023 paper is titled 'The Curse of Recursion: Training on Generated Data Makes Models Forget'.",
        "name",
        ("model collapse training on generated data", "Shumailov curse of recursion paper", "model collapse Nature 2024"),
    ),
]

BY_TEXT = {q.question: q for q in QUESTIONS}
QUERY_INDEX = {qq: q for q in QUESTIONS for qq in q.queries}
EXAMPLES = [q.question for q in QUESTIONS]

# (template, source_type, relevant, difficulty, fact kind for page-read, site, titles, snippet, excerpt)
# fact kind: "definition" | "date" | "extra" | None (the excerpt does not answer the question)
TEMPLATES: list[tuple[str, str, bool, str, str | None, str, tuple[str, str, str], str, str]] = [
    (
        "r-paper",
        "paper",
        True,
        "easy",
        "extra",
        "papers",
        ("{Topic}: the original paper", "{Topic} (arXiv preprint)", "Paper: {Topic}"),
        "Abstract. {date}",
        "From the abstract and results: {extra}",
    ),
    (
        "r-docs",
        "docs",
        True,
        "easy",
        "definition",
        "docs",
        ("{Topic} | Documentation", "{Topic} - user guide", "Concepts: {topic}"),
        "Reference documentation for {topic}.",
        "Overview. {definition}",
    ),
    (
        "r-tutorial",
        "blog",
        True,
        "easy",
        "definition",
        "blog",
        ("A gentle introduction to {topic}", "{Topic}, explained simply", "Understanding {topic} in 5 minutes"),
        "A beginner-friendly walkthrough of {topic}.",
        "In short: {definition}",
    ),
    (
        "r-history",
        "blog",
        True,
        "easy",
        "date",
        "history",
        ("A short history of {topic}", "Where {topic} came from", "{Topic}: origins"),
        "How {topic} came about.",
        "Background: {date}",
    ),
    (
        "r-survey",
        "paper",
        True,
        "hard",
        "extra",
        "survey",
        ("A survey of efficient deep learning methods", "Efficient LLMs: a review", "Recent advances in model efficiency"),
        "This survey covers many methods, including {topic}.",
        "Section 4 discusses {topic}. {extra}",
    ),
    (
        "r-forum-good",
        "forum",
        True,
        "hard",
        "definition",
        "forum",
        ("Can someone explain {topic}?", "ELI5: {topic}", "Question about {topic}"),
        "Top answer: here is how I think about it...",
        "Accepted answer: {definition}",
    ),
    (
        "r-forum-off",
        "forum",
        False,
        "hard",
        None,
        "forum",
        ("{Topic} install error, please help", "Build fails after upgrading, mentions {topic}", "Weird crash in my project"),
        "Getting a dependency error when I install the library.",
        "Tried reinstalling the package and clearing the cache, still the same stack trace. Any ideas?",
    ),
    (
        "r-news-off",
        "news",
        False,
        "easy",
        None,
        "news",
        ("Tech stocks rally as AI spending grows", "Chipmakers report record quarter", "Cloud providers expand data centres"),
        "Markets rose on Tuesday as investors bet on AI demand.",
        "Shares of several chipmakers closed higher after strong earnings reports.",
    ),
    (
        "r-listicle",
        "other",
        False,
        "hard",
        None,
        "listicles",
        ("Top 10 AI tools you need this year", "15 AI tricks that will change your life", "The only AI guide you will ever need"),
        "Number 7 will surprise you. Sign up for our newsletter.",
        "Subscribe now to unlock the full list and get our free ebook.",
    ),
    (
        "r-jobs",
        "other",
        False,
        "easy",
        None,
        "jobs",
        ("Hiring: ML engineer with {topic} experience", "Job: research engineer (remote)", "Careers at an AI startup"),
        "We are hiring engineers to join our platform team.",
        "Requirements: 3+ years of Python experience. Competitive salary and benefits.",
    ),
    (
        "r-news-on",
        "news",
        True,
        "hard",
        "date",
        "news",
        ("Why {topic} matters for cheaper AI", "{Topic} is getting attention again", "Explainer: {topic}"),
        "Researchers keep coming back to an older idea.",
        "The idea is not new. {date}",
    ),
    (
        "r-cookie",
        "other",
        False,
        "hard",
        None,
        "portal",
        ("{Topic} - sign in", "{Topic} | members area", "Access denied"),
        "Please enable cookies to continue.",
        "This site uses cookies. Accept all cookies to continue to the page.",
    ),
]


@dataclass
class ResearchItem:
    """One distillable request in the research world: a triage or a read of one result."""

    text: str
    template: str
    difficulty: str
    task: str
    labels: dict[str, Any]
    key_fact: str = ""
    messages_: list[dict[str, str]] = field(default_factory=list)
    params = None  # sent without generation parameters (sim/world.py `Item.params`); not a dataclass field

    @property
    def label_space(self) -> dict[str, list[Any]]:
        if self.task == "source-triage":
            return {"relevant": [True, False], "source_type": SOURCE_TYPES}
        return {"answers_question": [True, False], "fact_type": FACT_TYPES}

    @property
    def fragile(self) -> bool:
        """Slices a corrupted deploy breaks (fault injection)."""
        if self.task == "source-triage":
            return self.labels["source_type"] in ("paper", "docs")
        return self.labels["fact_type"] == "date"

    def answer(self, labels: dict[str, Any]) -> str:
        if self.task == "source-triage":
            reason = "It covers the question directly." if labels["relevant"] else "It does not address the question."
            return json.dumps({"relevant": labels["relevant"], "source_type": labels["source_type"], "reason": reason})
        fact = self.key_fact if labels["answers_question"] and labels["fact_type"] != "none" else ""
        return json.dumps({"answers_question": labels["answers_question"], "fact_type": labels["fact_type"], "key_fact": fact})

    def messages(self) -> list[dict[str, str]]:
        return self.messages_


@dataclass(frozen=True)
class SearchHit:
    id: str
    question: str
    template: str
    title: str
    url: str
    snippet: str
    excerpt: str


REGISTRY: dict[str, ResearchItem] = {}
POOL: dict[str, list[SearchHit]] = {}  # question key -> simulated results
VARIANTS = 6  # result variants per template and question (different pages on similar sites)


def phrasings(q: Question) -> list[str]:
    """Ways users ask the same thing. The first is the canonical example question."""
    return [
        q.question,
        f"Explain {q.topic} in simple terms.",
        f"Give me a short overview of {q.topic}.",
        f"Tell me about {q.topic}.",
    ]


def _fill(s: str, q: Question) -> str:
    topic = q.topic
    return s.format(topic=topic, Topic=topic[0].upper() + topic[1:], definition=q.definition, date=q.date, extra=q.extra)


def _build() -> None:
    for q in QUESTIONS:
        hits = []
        for tid, stype, relevant, diff, kind, site, titles, snip, exc in TEMPLATES:
            for v in range(VARIANTS):
                title = _fill(titles[v % len(titles)], q)
                url = f"https://{site}.example.org/{q.key}/{tid[2:]}-{v + 1}"
                hit = SearchHit(f"{q.key}:{tid}:{v}", q.key, tid, title, url, _fill(snip, q), _fill(exc, q))
                hits.append(hit)
                if kind is None:
                    fact_type, fact = "none", ""
                else:
                    fact_type = q.extra_type if kind == "extra" else kind
                    fact = {"definition": q.definition, "date": q.date, "extra": q.extra}[kind]
                for asked in phrasings(q):
                    tm = triage_messages(asked, hit.title, hit.url, hit.snippet)
                    REGISTRY[tm[-1]["content"]] = ResearchItem(
                        tm[-1]["content"], tid, diff, "source-triage", {"relevant": relevant, "source_type": stype}, messages_=tm
                    )
                    rm = read_messages(asked, hit.title, hit.url, hit.excerpt)
                    REGISTRY[rm[-1]["content"]] = ResearchItem(
                        rm[-1]["content"],
                        f"{tid}.read",
                        diff,
                        "page-read",
                        {"answers_question": kind is not None, "fact_type": fact_type},
                        key_fact=fact,
                        messages_=rm,
                    )
        POOL[q.key] = hits


_build()


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def search(query: str, max_results: int = 4) -> list[SearchHit]:
    """Simulated search: a deterministic mix of good and bad results for the query's question."""
    q = QUERY_INDEX.get(query) or BY_TEXT.get(query)
    if q is None:
        return []
    pool = POOL[q.key]
    return _rng("search", query).sample(pool, min(max_results, len(pool)))


def lookup(messages: list[dict[str, str]]) -> ResearchItem:
    user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    return REGISTRY[user]


class ResearchStream:
    """Agent traffic for the simulator: triage and read calls over the research world.
    Users phrase each question several ways. Questions 7 and 8 only appear from `drift_day`
    (new topics the student has not seen)."""

    def __init__(self, seed: int = 7, drift_day: int | None = 5, drift_share: float = 0.2):
        self.rng = random.Random(seed)
        self.drift_day = drift_day
        self.drift_share = drift_share

    def sample(self, day: int) -> ResearchItem:
        qs = QUESTIONS[:6]
        if self.drift_day is not None and day >= self.drift_day and self.rng.random() < self.drift_share:
            qs = QUESTIONS[6:]
        q = self.rng.choice(qs)
        asked = self.rng.choice(phrasings(q))
        hit = self.rng.choice(POOL[q.key])
        if self.rng.random() < 0.6:
            text = triage_messages(asked, hit.title, hit.url, hit.snippet)[-1]["content"]
        else:
            text = read_messages(asked, hit.title, hit.url, hit.excerpt)[-1]["content"]
        return REGISTRY[text]


def plan_answer(question: str) -> str:
    q = BY_TEXT.get(question)
    queries = list(q.queries) if q else [question]
    return json.dumps({"queries": queries})


def write_answer(facts: list[dict[str, Any]]) -> str:
    """Mock writer: stitch the extracted facts into a short cited answer."""
    seen, parts, used = set(), [], []
    for f in facts:
        text = (f.get("fact") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(f"{text.rstrip('.')} [{f['n']}].")
        used.append(f["n"])
        if len(parts) == 3:
            break
    if not parts:
        return json.dumps({"answer": "The sources found do not answer the question.", "used": []})
    return json.dumps({"answer": " ".join(parts), "used": used})
