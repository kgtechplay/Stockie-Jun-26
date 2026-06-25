from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.news_sentiment.schemas import SentimentResult

_LABEL_TO_SCORE = {
    "positive": 1.0,
    "neutral": 0.0,
    "negative": -1.0,
}

_POSITIVE_WORDS = {
    "rally", "gain", "gains", "surge", "surges", "higher", "upbeat", "positive",
    "growth", "eases", "cooling", "cut", "cuts", "record", "strong", "boost",
    "optimism", "inflows", "rebound", "recover", "recovery",
}
_NEGATIVE_WORDS = {
    "fall", "falls", "slump", "slumps", "drop", "drops", "lower", "weak", "negative",
    "inflation", "hike", "hikes", "selloff", "outflows", "crash", "war", "risk",
    "risks", "surges", "spike", "spikes", "pressure", "loss", "losses", "concern",
}


@dataclass
class FinBertSentimentScorer:
    model_name: str = "ProsusAI/finbert"
    use_transformers: bool = True
    batch_size: int = 16
    max_length: int = 256

    def __post_init__(self) -> None:
        self._pipeline = None
        if not self.use_transformers:
            return
        try:
            from transformers import (  # type: ignore[import-not-found]
                BertForSequenceClassification,
                BertTokenizer,
                pipeline,
            )

            tokenizer = BertTokenizer.from_pretrained(self.model_name)
            model = BertForSequenceClassification.from_pretrained(self.model_name)
            self._pipeline = pipeline("text-classification", model=model, tokenizer=tokenizer)
        except Exception as exc:  # noqa: BLE001 - fallback keeps the scaffold runnable.
            print(f"[WARN] FinBERT unavailable; using lexical fallback: {exc}")
            self._pipeline = None

    def score(self, text: str) -> SentimentResult:
        clean = " ".join((text or "").split())
        if not clean:
            return SentimentResult("neutral", 0.0, 0.0, "empty_text")
        if self._pipeline is not None:
            result = self._pipeline(
                clean,
                truncation=True,
                max_length=self.max_length,
            )[0]
            return self._from_finbert_result(result)
        return self._lexical_score(clean)

    def score_many(self, texts: list[str]) -> list[SentimentResult]:
        clean_texts = [" ".join((text or "").split()) for text in texts]
        if self._pipeline is None:
            return [self._lexical_score(text) if text else SentimentResult("neutral", 0.0, 0.0, "empty_text")
                    for text in clean_texts]

        results: list[SentimentResult] = []
        pending_indexes: list[int] = []
        pending_texts: list[str] = []
        for index, text in enumerate(clean_texts):
            if not text:
                results.append(SentimentResult("neutral", 0.0, 0.0, "empty_text"))
            else:
                pending_indexes.append(index)
                pending_texts.append(text)
                results.append(SentimentResult("neutral", 0.0, 0.0, "pending"))

        if pending_texts:
            raw_results = self._pipeline(
                pending_texts,
                truncation=True,
                max_length=self.max_length,
                batch_size=self.batch_size,
            )
            for index, raw_result in zip(pending_indexes, raw_results):
                results[index] = self._from_finbert_result(raw_result)
        return results

    def _from_finbert_result(self, result: dict[str, Any]) -> SentimentResult:
        label = str(result.get("label", "neutral")).lower().strip()
        confidence = float(result.get("score", 0.0))
        if label not in _LABEL_TO_SCORE:
            label = "neutral"
        return SentimentResult(label, _LABEL_TO_SCORE[label], confidence, f"finbert:{self.model_name}")

    def _lexical_score(self, text: str) -> SentimentResult:
        tokens = set(re.findall(r"[a-zA-Z]+", text.lower()))
        pos = len(tokens & _POSITIVE_WORDS)
        neg = len(tokens & _NEGATIVE_WORDS)
        margin = pos - neg
        if margin > 0:
            label = "positive"
        elif margin < 0:
            label = "negative"
        else:
            label = "neutral"
        confidence = min(0.75, 0.45 + 0.10 * abs(margin)) if margin else 0.40
        return SentimentResult(label, _LABEL_TO_SCORE[label], confidence, "lexical_fallback")
