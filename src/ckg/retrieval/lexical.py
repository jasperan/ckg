"""Lexical anchor selection — which nodes does the query name?

Nodes are matched on two fields: their *name* (path components of a file,
qualified name of a symbol) and their descriptive *text* (signature, first
docstring line). Tokens are split on case and punctuation, lightly stemmed,
and weighted by rarity, so a word that appears in every node contributes
nothing and ``DatabaseConnection`` is found by "database connection".

The postings for a graph are computed once at build time (``build_index``)
and stored with the graph, so a query costs a few dictionary lookups instead
of re-tokenising every node.
"""

from __future__ import annotations

import math
import re
import unicodedata

INDEX_VERSION = 1
NAME_WEIGHT = 2.0
# A match is kept only if it scores at least this fraction of the best one.
_RELATIVE_FLOOR = 0.35
# In a graph of at least _COMMON_MIN_NODES nodes, a token found in more than
# this share of them names nothing in particular.
_COMMON_SHARE = 0.4
_COMMON_MIN_NODES = 20

_STOPWORDS = frozenset({
    # English glue
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "is",
    "it", "be", "as", "by", "with", "from", "that", "this", "if", "are", "was",
    "not", "when", "should", "does", "do", "can", "into", "via", "so", "we",
    # Words every Python node carries
    "py", "python", "module", "def", "class", "async", "self", "cls", "none",
    "init", "return",
})
# Task verbs in a prompt ("fix the …", "add support for …") describe the work,
# not the code, yet match every ``add_*``/``fix_*`` symbol.
_QUERY_STOPWORDS = frozenset({
    "fix", "add", "implement", "change", "make", "bug", "issue", "feature",
    "refactor", "support", "new", "use", "update", "improve", "please", "need",
})

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_RE = re.compile(r"[^\W_]+")
_SUFFIXES = (
    ("ies", "y"), ("sses", "ss"), ("ations", "at"), ("ation", "at"),
    ("ions", ""), ("ion", ""), ("ing", ""), ("ers", ""), ("er", ""),
    ("ed", ""), ("es", ""), ("s", ""),
)


def stem(token: str) -> str:
    """Conflate simple inflections (``connections``/``connecting`` → ``connect``).

    Deliberately crude: it only has to map a query word and a code word to
    the same key, not produce a real word.
    """
    for suffix, replacement in _SUFFIXES:
        if not token.endswith(suffix):
            continue
        if suffix == "s" and token.endswith(("ss", "us", "is")):
            break
        base = token[: len(token) - len(suffix)] + replacement
        if len(base) >= 3:
            token = base
            break
    if len(token) > 3 and token.endswith("e"):
        token = token[:-1]
    return token


def tokenize(text: str) -> set[str]:
    """Stemmed word tokens of ``text``, without stopwords."""
    if not text.isascii():
        text = "".join(
            ch for ch in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(ch)
        )
    out: set[str] = set()
    for word in _WORD_RE.findall(_CAMEL_RE.sub(" ", text)):
        token = word.lower()
        if len(token) >= 2 and token not in _STOPWORDS:
            out.add(stem(token))
    return out


def name_of(node_id: str) -> str:
    """The part of a node id that names it: a file's path, a symbol's qualname."""
    if node_id.startswith("sym:"):
        return node_id.split("::", 1)[-1]
    return node_id.removeprefix("file:").removesuffix(".py")


def build_index(nodes: dict[str, dict]) -> dict:
    """Token → node ordinals, separately for names and descriptive text.

    Ordinals refer to the iteration order of ``nodes``, which JSON round-trips
    preserve.
    """
    name: dict[str, list[int]] = {}
    text: dict[str, list[int]] = {}
    for i, (nid, node) in enumerate(nodes.items()):
        name_tokens = tokenize(name_of(nid))
        for token in name_tokens:
            name.setdefault(token, []).append(i)
        for token in tokenize(node.get("text", "")) - name_tokens:
            text.setdefault(token, []).append(i)
    return {"v": INDEX_VERSION, "n": len(nodes), "name": name, "text": text}


def _usable(index: object, n_nodes: int) -> bool:
    return (
        isinstance(index, dict)
        and index.get("v") == INDEX_VERSION
        and index.get("n") == n_nodes
        and isinstance(index.get("name"), dict)
        and isinstance(index.get("text"), dict)
    )


def score_nodes(query: str, nodes: dict[str, dict], index: dict | None = None) -> dict[str, float]:
    """Rarity-weighted match score of every node the query names (others absent)."""
    n = len(nodes)
    if not n:
        return {}
    if not _usable(index, n):
        index = build_index(nodes)
    assert index is not None
    scores: dict[int, float] = {}
    for token in sorted(tokenize(query) - _QUERY_STOPWORDS):
        in_name = index["name"].get(token, ())
        in_text = index["text"].get(token, ())
        df = len(in_name) + len(in_text)
        if not df or (n >= _COMMON_MIN_NODES and df > _COMMON_SHARE * n):
            continue
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for i in in_name:
            scores[i] = scores.get(i, 0.0) + NAME_WEIGHT * idf
        for i in in_text:
            scores[i] = scores.get(i, 0.0) + idf
    ids = list(nodes)
    return {ids[i]: s for i, s in scores.items() if 0 <= i < n}


def seed_from_query(
    query: str,
    nodes: dict[str, dict],
    *,
    k: int = 5,
    index: dict | None = None,
) -> dict[str, float]:
    """Pick up to ``k`` seed nodes for the query, weighted by match strength.

    This is the *lexical anchor* step. Only matches close to the best one are
    kept, so one strong hit is not diluted by a tail of incidental ones.
    Ties are broken by node id; a query that names nothing returns ``{}``.
    """
    if k <= 0:
        return {}
    scored = score_nodes(query, nodes, index)
    if not scored:
        return {}
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    floor = ranked[0][1] * _RELATIVE_FLOOR
    return {nid: score for nid, score in ranked[:k] if score >= floor}
