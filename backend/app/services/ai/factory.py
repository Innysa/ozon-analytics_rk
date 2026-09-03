from functools import lru_cache

from app.core.config import Settings, get_settings
from app.services.ai.base import AIProvider
from app.services.ai.demo_provider import DemoProvider


def _build_provider(settings: Settings) -> AIProvider:
    if settings.DEMO_MODE or settings.AI_PROVIDER == "demo":
        return DemoProvider()
    if settings.AI_PROVIDER == "yandex":
        from app.services.ai.yandex_provider import YandexAIProvider  # local import: keeps httpx call surface isolated

        return YandexAIProvider(settings)
    raise ValueError(f"Неизвестный AI_PROVIDER: {settings.AI_PROVIDER}")


@lru_cache
def get_ai_provider() -> AIProvider:
    return _build_provider(get_settings())
