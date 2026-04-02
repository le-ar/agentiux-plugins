from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_command_phrase(phrase: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", (phrase or "").strip()).casefold()
    return re.sub(r"\s+", " ", normalized)


def tokenize_text(value: str | None) -> list[str]:
    normalized = normalize_command_phrase(value)
    if not normalized:
        return []
    return sorted(
        {
            token
            for token in re.split(r"(?:[^\w]+|_+)", normalized, flags=re.UNICODE)
            if len(token) >= 2 and token.strip("_")
        }
    )


def short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def match_keywords(normalized_text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in normalized_text]


def expand_token_set(values: list[str], synonyms: dict[str, list[str]] | None = None) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        normalized = normalize_command_phrase(value)
        if not normalized:
            continue
        expanded.add(normalized)
        for synonym in (synonyms or {}).get(normalized, []):
            expanded.add(normalize_command_phrase(synonym))
    return expanded


def score_token_match(
    query_tokens: list[str],
    candidate_tokens: list[str],
    weight: int,
    *,
    synonyms: dict[str, list[str]] | None = None,
) -> tuple[int, list[str]]:
    query_set = expand_token_set(query_tokens, synonyms)
    candidate_set = expand_token_set(candidate_tokens, synonyms)
    if not query_set or not candidate_set:
        return 0, []
    matches = sorted(query_set.intersection(candidate_set))
    return len(matches) * weight, matches


def score_preexpanded_query_match(
    query_set: set[str],
    candidate_tokens: list[str],
    weight: int,
    *,
    synonyms: dict[str, list[str]] | None = None,
) -> tuple[int, list[str]]:
    if not query_set:
        return 0, []
    candidate_set = expand_token_set(candidate_tokens, synonyms)
    if not candidate_set:
        return 0, []
    matches = sorted(query_set.intersection(candidate_set))
    return len(matches) * weight, matches


def score_preexpanded_set_match(
    query_set: set[str],
    candidate_set: set[str],
    weight: int,
) -> tuple[int, list[str]]:
    if not query_set or not candidate_set:
        return 0, []
    matches = sorted(query_set.intersection(candidate_set))
    return len(matches) * weight, matches
