from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

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

HF_FINBERT_MODEL = "ProsusAI/finbert"
HF_SERVERLESS_URL = "https://api-inference.huggingface.co/models"


def build_sentiment_scorer(use_transformers: bool = True):
    """Select sentiment scorer from env.

    NEWS_SENTIMENT_SCORER=hf_finbert uses Hugging Face serverless inference and
    avoids loading FinBERT locally. Any other value preserves the current local
    FinBERT/lexical fallback behavior.
    """
    scorer = (os.getenv("NEWS_SENTIMENT_SCORER") or "").strip().lower()
    if scorer in {"hf", "hf_finbert", "huggingface", "huggingface_finbert"}:
        return HuggingFaceFinBertSentimentScorer()
    return FinBertSentimentScorer(use_transformers=use_transformers)


@dataclass
class HuggingFaceFinBertSentimentScorer:
    model_name: str = HF_FINBERT_MODEL
    timeout_seconds: int = 60
    max_chars: int = 4000

    def __post_init__(self) -> None:
        self.token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN") or ""
        self.model_name = os.getenv("HF_INFERENCE_MODEL") or self.model_name
        self.endpoint_url = (
            os.getenv("HF_INFERENCE_URL")
            or f"{HF_SERVERLESS_URL}/{self.model_name}"
        )
        self._fallback = None
        if not self.token:
            print("[WARN] HF_TOKEN is not set; using configured local/lexical fallback for sentiment.")

    def score(self, text: str) -> SentimentResult:
        clean = " ".join((text or "").split())
        if not clean:
            return SentimentResult("neutral", 0.0, 0.0, "empty_text")
        if not self.token:
            return self.fallback.score(clean)

        try:
            response = requests.post(
                self.endpoint_url,
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "inputs": clean[: self.max_chars],
                    "parameters": {"top_k": 3},
                    "options": {"wait_for_model": True},
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return self._from_hf_response(response.json())
        except Exception as exc:  # noqa: BLE001 - daily job should degrade, not die.
            print(f"[WARN] Hugging Face FinBERT call failed; using configured local/lexical fallback: {exc}")
            return self.fallback.score(clean)

    def score_many(self, texts: list[str]) -> list[SentimentResult]:
        return [self.score(text) for text in texts]

    def _from_hf_response(self, body: Any) -> SentimentResult:
        candidates = _flatten_hf_candidates(body)
        if not candidates:
            raise ValueError(f"Unexpected Hugging Face response: {body!r}")

        best = max(candidates, key=lambda item: float(item.get("score") or 0.0))
        label = _normalize_label(str(best.get("label") or "neutral"))
        confidence = float(best.get("score") or 0.0)
        return SentimentResult(
            label,
            _LABEL_TO_SCORE[label],
            confidence,
            f"hf_serverless:{self.model_name}",
        )

    @property
    def fallback(self) -> "FinBertSentimentScorer":
        if self._fallback is None:
            self._fallback = _build_hf_fallback_scorer()
        return self._fallback


@dataclass
class FinBertSentimentScorer:
    model_name: str = "ProsusAI/finbert"
    use_transformers: bool = True
    batch_size: int = 16
    max_length: int = 256

    def __post_init__(self) -> None:
        self._pipeline = None
        self.model_name = (
            os.getenv("FINBERT_LOCAL_MODEL_PATH")
            or os.getenv("FINBERT_MODEL_PATH")
            or self.model_name
        )
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


def _flatten_hf_candidates(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict) and "error" in body:
        raise RuntimeError(str(body["error"]))
    if isinstance(body, list):
        if body and isinstance(body[0], list):
            return [item for group in body for item in group if isinstance(item, dict)]
        return [item for item in body if isinstance(item, dict)]
    return []


def _normalize_label(label: str) -> str:
    clean = label.lower().strip()
    if clean in _LABEL_TO_SCORE:
        return clean
    if clean.endswith("positive") or clean in {"label_2", "bullish"}:
        return "positive"
    if clean.endswith("negative") or clean in {"label_0", "bearish"}:
        return "negative"
    return "neutral"


def _build_hf_fallback_scorer() -> FinBertSentimentScorer:
    fallback_mode = (os.getenv("HF_FINBERT_FALLBACK") or "").strip().lower()
    if fallback_mode in {"local", "local_finbert", "finbert"}:
        local_path = os.getenv("FINBERT_LOCAL_MODEL_PATH") or os.getenv("FINBERT_MODEL_PATH") or HF_FINBERT_MODEL
        print(f"[INFO] HF fallback configured as local FinBERT: {local_path}")
        return FinBertSentimentScorer(model_name=local_path, use_transformers=True)
    return FinBertSentimentScorer(use_transformers=False)
