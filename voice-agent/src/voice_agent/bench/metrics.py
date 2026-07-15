from __future__ import annotations

from ..util.wer import cer, partial_churn, wer


def compute_metrics(reference: str, final_text: str, refined_text: str | None, partials: list[str]) -> dict:
    return {
        "wer_final": wer(reference, final_text),
        "cer_final": cer(reference, final_text),
        "wer_refined": wer(reference, refined_text or ""),
        "cer_refined": cer(reference, refined_text or ""),
        "partial_churn": partial_churn(partials),
    }
