from __future__ import annotations

from typing import List, Tuple

from .textnorm import normalize


def _edit_distance(ref: List[str], hyp: List[str]) -> int:
    dp = [[0] * (len(hyp) + 1) for _ in range(len(ref) + 1)]
    for i in range(len(ref) + 1):
        dp[i][0] = i
    for j in range(len(hyp) + 1):
        dp[0][j] = j
    for i in range(1, len(ref) + 1):
        for j in range(1, len(hyp) + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[-1][-1]


def wer(ref: str, hyp: str) -> float:
    ref_tokens = normalize(ref).split()
    hyp_tokens = normalize(hyp).split()
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _edit_distance(ref_tokens, hyp_tokens) / len(ref_tokens)


def cer(ref: str, hyp: str) -> float:
    ref_chars = list(normalize(ref).replace(" ", ""))
    hyp_chars = list(normalize(hyp).replace(" ", ""))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _edit_distance(ref_chars, hyp_chars) / len(ref_chars)


def partial_churn(partials: List[str]) -> float:
    if len(partials) < 2:
        return 0.0
    total = 0
    for i in range(1, len(partials)):
        total += _edit_distance(list(partials[i - 1]), list(partials[i]))
    return total / (len(partials) - 1)
