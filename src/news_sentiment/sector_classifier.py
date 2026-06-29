from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from src.news_sentiment.config import NIFTY50_SECTOR_DEFINITIONS
from src.news_sentiment.schemas import SectorTag

SUPPORTED_SECTOR_KEYS = tuple(definition.key for definition in NIFTY50_SECTOR_DEFINITIONS) + ("broad_market",)

SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "financial_services": (
        "bank", "banks", "banking", "nbfc", "hdfc", "icici", "axis", "sbi", "credit",
        "loan", "loans", "rbi", "rate", "rates", "bond", "bonds", "yield", "yields",
    ),
    "information_technology": (
        "it", "software", "technology", "tech", "infosys", "tcs", "wipro", "hcltech",
        "nasdaq", "ai", "cloud", "dollar revenue",
    ),
    "oil_gas": (
        "oil", "crude", "brent", "wti", "gas", "ongc", "reliance", "fuel", "diesel",
        "petrol", "refining", "opec",
    ),
    "fmcg": (
        "fmcg", "consumer staples", "hul", "itc", "nestle", "britannia", "rural demand",
    ),
    "automobile": (
        "auto", "automobile", "ev", "vehicle", "vehicles", "maruti", "tata motors",
        "mahindra", "two-wheeler", "tractor",
    ),
    "healthcare": (
        "pharma", "healthcare", "drug", "drugs", "fda", "hospital", "sun pharma", "cipla",
        "dr reddy", "divis",
    ),
    "metals": (
        "metal", "metals", "steel", "aluminium", "copper", "iron ore", "hindalco", "tata steel",
        "jsw steel",
    ),
    "consumer_durables": (
        "consumer durables", "white goods", "jewellery", "titan", "havells", "voltas",
    ),
    "telecom": ("telecom", "tariff", "tariffs", "airtel", "jio", "vodafone", "5g"),
    "construction": (
        "construction", "infrastructure", "infra", "cement", "ultratech", "lt", "larsen",
        "roads", "capex",
    ),
    "power": ("power", "electricity", "renewable", "ntpc", "power grid", "adani green"),
    "services": ("services", "logistics", "aviation", "airline", "indigo", "ports"),
    "realty": ("real estate", "realty", "property", "housing", "dlf", "godrej properties"),
    "broad_market": (
        "nifty", "sensex", "market", "markets", "equities", "stocks", "fii", "dii",
        "rupee", "inflation", "fed", "global cues", "gdp", "budget",
    ),
}


def classify_sectors(text: str, max_tags: int = 3) -> list[SectorTag]:
    return KeywordSectorClassifier().classify(text, max_tags=max_tags)


class KeywordSectorClassifier:
    def classify(self, text: str, max_tags: int = 3) -> list[SectorTag]:
        return _classify_sectors_keyword(text, max_tags=max_tags)

    def classify_many(self, texts: list[str], max_tags: int = 3) -> list[list[SectorTag]]:
        return [self.classify(text, max_tags=max_tags) for text in texts]


@dataclass
class LlmSectorClassifier:
    provider: str = "azure"
    timeout_seconds: int = 90
    max_batch_size: int = 10
    temperature: float = 0.0

    def __post_init__(self) -> None:
        self.provider = (os.getenv("NEWS_SECTOR_LLM_PROVIDER") or self.provider).lower().strip()
        self.azure_endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_AI_ENDPOINT") or "").rstrip("/")
        self.azure_api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_AI_API_KEY") or ""
        self.azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("AZURE_AI_DEPLOYMENT") or ""
        self.azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("AZURE_AI_API_VERSION") or "2024-02-15-preview"

    @property
    def resolved_provider(self) -> str | None:
        if self.provider == "azure":
            return "azure" if self._azure_configured else None
        return None

    @property
    def _azure_configured(self) -> bool:
        return bool(self.azure_endpoint and self.azure_api_key and self.azure_deployment)

    def classify(self, text: str, max_tags: int = 3) -> list[SectorTag]:
        return self.classify_many([text], max_tags=max_tags)[0]

    def classify_many(self, texts: list[str], max_tags: int = 3) -> list[list[SectorTag]]:
        provider = self.resolved_provider
        if provider is None:
            print(
                "[WARN] Azure OpenAI sector classifier is not configured; "
                "using keyword fallback. Required: AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT."
            )
            return KeywordSectorClassifier().classify_many(texts, max_tags=max_tags)

        results: list[list[SectorTag]] = []
        for start in range(0, len(texts), self.max_batch_size):
            batch = texts[start:start + self.max_batch_size]
            try:
                results.extend(self._classify_batch(batch, provider, max_tags=max_tags))
            except Exception as exc:  # noqa: BLE001 - keep the daily pipeline resilient.
                print(f"[WARN] LLM sector classifier failed; using keyword fallback for batch: {exc}")
                results.extend(KeywordSectorClassifier().classify_many(batch, max_tags=max_tags))
        return results

    def _classify_batch(self, texts: list[str], provider: str, max_tags: int = 3) -> list[list[SectorTag]]:
        payload = {
            "sectors": _sector_prompt_options(),
            "articles": [
                {"index": index, "text": " ".join((text or "").split())[:2500]}
                for index, text in enumerate(texts)
            ],
        }
        request_body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": _llm_system_prompt(max_tags)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(
            self._chat_url(provider),
            headers=self._headers(provider),
            params=self._params(provider),
            json=request_body,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"{provider} sector call failed: {response.status_code} {response.text}")
        content = response.json()["choices"][0]["message"]["content"]
        body = _parse_json_object(content)
        by_index = _tags_by_index(body, max_tags=max_tags)
        return [by_index.get(index) or KeywordSectorClassifier().classify(text, max_tags=max_tags)
                for index, text in enumerate(texts)]

    def _chat_url(self, provider: str) -> str:
        if provider == "azure":
            return f"{self.azure_endpoint}/openai/deployments/{self.azure_deployment}/chat/completions"
        raise ValueError(f"Unsupported sector LLM provider: {provider}")

    def _headers(self, provider: str) -> dict[str, str]:
        if provider == "azure":
            return {"api-key": self.azure_api_key, "Content-Type": "application/json"}
        raise ValueError(f"Unsupported sector LLM provider: {provider}")

    def _params(self, provider: str) -> dict[str, str]:
        if provider == "azure":
            return {"api-version": self.azure_api_version}
        return {}


def _classify_sectors_keyword(text: str, max_tags: int = 3) -> list[SectorTag]:
    lowered = (text or "").lower()
    scores: list[tuple[str, int, list[str]]] = []
    for sector, terms in SECTOR_KEYWORDS.items():
        matched = [term for term in terms if _contains_term(lowered, term)]
        if matched:
            scores.append((sector, len(matched), matched))

    if not scores:
        return [SectorTag("broad_market", 0.35, [])]

    scores.sort(key=lambda item: item[1], reverse=True)
    top = scores[:max_tags]
    total = sum(score for _, score, _ in top) or 1
    return [SectorTag(sector, score / total, terms) for sector, score, terms in top]


def _contains_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _sector_prompt_options() -> list[dict[str, str]]:
    return [
        {"sector": definition.key, "label": definition.label}
        for definition in NIFTY50_SECTOR_DEFINITIONS
    ] + [{"sector": "broad_market", "label": "Broad market, macro, index, rates, currency, global cues"}]


def _llm_system_prompt(max_tags: int) -> str:
    return (
        "Classify each Indian market news article into NIFTY sector exposure buckets. "
        f"Return at most {max_tags} sectors per article. Use only the provided sector keys. "
        "Use broad_market for index-level, macro, rates, currency, global cues, FII/DII, or cross-sector news. "
        "Return only JSON with shape: {\"articles\":[{\"index\":0,\"sectors\":[{\"sector\":\"broad_market\",\"confidence\":1.0}]}]}. "
        "Confidence values must be positive and should reflect relative sector exposure."
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM sector classifier did not return a JSON object.")
    return parsed


def _tags_by_index(body: dict[str, Any], max_tags: int = 3) -> dict[int, list[SectorTag]]:
    articles = body.get("articles") or []
    out: dict[int, list[SectorTag]] = {}
    if not isinstance(articles, list):
        return out
    for article in articles:
        if not isinstance(article, dict):
            continue
        try:
            index = int(article.get("index"))
        except (TypeError, ValueError):
            continue
        out[index] = _normalize_llm_tags(article.get("sectors"), max_tags=max_tags)
    return out


def _normalize_llm_tags(raw_sectors: Any, max_tags: int = 3) -> list[SectorTag]:
    if not isinstance(raw_sectors, list):
        return []
    pairs: list[tuple[str, float]] = []
    for item in raw_sectors:
        if not isinstance(item, dict):
            continue
        sector = str(item.get("sector") or "").strip()
        if sector not in SUPPORTED_SECTOR_KEYS:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if confidence > 0:
            pairs.append((sector, min(1.0, confidence)))
    if not pairs:
        return []
    pairs.sort(key=lambda item: item[1], reverse=True)
    top = pairs[:max_tags]
    total = sum(confidence for _, confidence in top) or 1.0
    return [SectorTag(sector, confidence / total, ["llm"]) for sector, confidence in top]
