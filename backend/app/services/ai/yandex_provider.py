"""YandexAIProvider — talks to Yandex AI Studio's OpenAI-compatible Responses API.

Endpoint and auth verified against https://aistudio.yandex.ru/docs/ (Concepts /
API): base URL `https://ai.api.cloud.yandex.net/v1`, header
`Authorization: Api-Key <YANDEX_API_KEY>`, model addressed as a URI
`gpt://<YANDEX_FOLDER_ID>/<model>/<version>` (default version `latest`).

Yandex AI Studio's exact JSON response shape for the Responses API was not
independently confirmed at implementation time beyond the documented request
example, so `_extract_text` below tries several known OpenAI-Responses-API-
compatible shapes defensively instead of assuming one fixed path — if Yandex's
actual response differs, this raises a clear error rather than silently
fabricating output. Verify against https://aistudio.yandex.ru/docs/ before
depending on a new field.

Native structured-output support was not confirmed either, so structured JSON
is enforced by prompting (see app.services.ai.prompts) plus Pydantic
validation with a single repair round-trip, as required by the product spec.
"""
from __future__ import annotations

import json
import time

import httpx

from app.core.config import Settings
from app.core.pii_mask import mask_pii
from app.models.store_ai_settings import StoreAISettings
from app.services.ai.base import AIProvider
from app.services.ai.prompts import build_analyze_prompt, build_repair_prompt, build_rewrite_prompt
from app.services.ai.schemas import (
    AIUsage,
    AnalyzeReviewOutcome,
    ConnectionCheckResult,
    GenerateReplyOutcome,
    ReviewAnalysisResult,
)

RESPONSES_URL = "https://ai.api.cloud.yandex.net/v1/responses"

# Approximate YandexGPT pricing is per-1000-token and changes over time; without
# a confirmed current rate card we do not fabricate a cost figure per call.
# Leave estimated_cost_rub unset until a verified tariff is configured.


class YandexAIProvider(AIProvider):
    name = "yandex"

    def __init__(self, settings: Settings):
        if not settings.YANDEX_API_KEY or not settings.YANDEX_FOLDER_ID:
            raise ValueError("YANDEX_API_KEY и YANDEX_FOLDER_ID должны быть заданы для AI_PROVIDER=yandex")
        self._api_key = settings.YANDEX_API_KEY
        self._folder_id = settings.YANDEX_FOLDER_ID
        self._model = settings.YANDEX_MODEL
        self._client = httpx.Client(timeout=30.0)

    def _model_uri(self) -> str:
        return f"gpt://{self._folder_id}/{self._model}"

    def _call(self, prompt_text: str, *, max_output_tokens: int = 800) -> tuple[str, AIUsage]:
        started = time.monotonic()
        response = self._client.post(
            RESPONSES_URL,
            headers={"Authorization": f"Api-Key {self._api_key}", "Content-Type": "application/json"},
            json={
                "model": self._model_uri(),
                "temperature": 0.4,
                "max_output_tokens": max_output_tokens,
                "input": prompt_text,
            },
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            raise RuntimeError(f"Yandex AI Studio вернул ошибку {response.status_code}: {response.text[:500]}")
        data = response.json()
        text = self._extract_text(data)
        usage_raw = data.get("usage", {}) if isinstance(data, dict) else {}
        usage = AIUsage(
            model=self._model,
            prompt_tokens=usage_raw.get("input_tokens") or usage_raw.get("prompt_tokens"),
            completion_tokens=usage_raw.get("output_tokens") or usage_raw.get("completion_tokens"),
            latency_ms=latency_ms,
            estimated_cost_rub=None,
        )
        return text, usage

    @staticmethod
    def _extract_text(data: dict) -> str:
        if not isinstance(data, dict):
            raise RuntimeError("Неожиданный формат ответа Yandex AI Studio")
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                content = item.get("content") if isinstance(item, dict) else None
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            return part["text"]
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            if isinstance(message.get("content"), str):
                return message["content"]
        raise RuntimeError(
            "Не удалось извлечь текст из ответа Yandex AI Studio — формат ответа изменился, "
            "требуется сверка с документацией aistudio.yandex.ru"
        )

    @staticmethod
    def _parse_json_result(raw_text: str) -> dict:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        return json.loads(cleaned)

    def analyze_review(
        self,
        *,
        product_name: str | None,
        rating: int,
        text: str | None,
        pros: str | None,
        cons: str | None,
        store_settings: StoreAISettings | None,
    ) -> AnalyzeReviewOutcome:
        masked_text, masked_pros, masked_cons = mask_pii(text), mask_pii(pros), mask_pii(cons)
        prompt = build_analyze_prompt(
            product_name=product_name, rating=rating, text=masked_text, pros=masked_pros, cons=masked_cons,
            store_settings=store_settings,
        )
        try:
            raw_text, usage = self._call(prompt)
            try:
                parsed = self._parse_json_result(raw_text)
                result = ReviewAnalysisResult.model_validate(parsed)
            except Exception:
                repair_text, repair_usage = self._call(build_repair_prompt(raw_text))
                usage = AIUsage(
                    model=usage.model,
                    prompt_tokens=(usage.prompt_tokens or 0) + (repair_usage.prompt_tokens or 0),
                    completion_tokens=(usage.completion_tokens or 0) + (repair_usage.completion_tokens or 0),
                    latency_ms=(usage.latency_ms or 0) + (repair_usage.latency_ms or 0),
                )
                parsed = self._parse_json_result(repair_text)
                result = ReviewAnalysisResult.model_validate(parsed)
            return AnalyzeReviewOutcome(result=result, usage=usage, success=True)
        except Exception as exc:
            return AnalyzeReviewOutcome(
                result=None, usage=AIUsage(model=self._model), success=False, error_message=str(exc)
            )

    def generate_review_reply(
        self,
        *,
        product_name: str | None,
        rating: int,
        text: str | None,
        pros: str | None,
        cons: str | None,
        store_settings: StoreAISettings | None,
    ) -> GenerateReplyOutcome:
        outcome = self.analyze_review(
            product_name=product_name, rating=rating, text=text, pros=pros, cons=cons, store_settings=store_settings
        )
        if not outcome.success or outcome.result is None:
            return GenerateReplyOutcome(reply_text=None, usage=outcome.usage, success=False, error_message=outcome.error_message)
        return GenerateReplyOutcome(reply_text=outcome.result.reply_text, usage=outcome.usage, success=True)

    def rewrite_review_reply(
        self, *, existing_reply: str, instruction: str, store_settings: StoreAISettings | None
    ) -> GenerateReplyOutcome:
        prompt = build_rewrite_prompt(existing_reply, instruction, store_settings)
        try:
            raw_text, usage = self._call(prompt, max_output_tokens=400)
            return GenerateReplyOutcome(reply_text=raw_text.strip(), usage=usage, success=True)
        except Exception as exc:
            return GenerateReplyOutcome(reply_text=None, usage=AIUsage(model=self._model), success=False, error_message=str(exc))

    def check_connection(self) -> ConnectionCheckResult:
        try:
            self._call("Ответь одним словом: OK", max_output_tokens=16)
            return ConnectionCheckResult(ok=True, message="Подключение к Yandex AI Studio успешно")
        except Exception as exc:
            return ConnectionCheckResult(ok=False, message=str(exc))
