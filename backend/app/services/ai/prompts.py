"""Prompt construction shared by every AIProvider implementation.

Keeping the rules text in one place means every provider (Yandex today,
GigaChat/OpenAI/etc later) behaves consistently, since the business rules
live here rather than duplicated per-provider.
"""
from __future__ import annotations

from app.models.store_ai_settings import StoreAISettings

REPLY_RULES = """\
Правила для ответа покупателю (обязательны):
- Пиши только на русском языке.
- Учитывай оценку, текст отзыва, указанные достоинства и недостатки, и конкретный товар.
- Ответ должен звучать естественно и по-человечески, не быть шаблонным под копирку.
- Не начинай все ответы одинаковой фразой.
- Не пересказывай отзыв целиком.
- Не придумывай свойства товара, которых нет в предоставленных фактах о товаре.
- Не обещай компенсацию, возврат денег или иные материальные блага, если это прямо не разрешено настройками магазина.
- Не признавай юридическую или иную ответственность магазина.
- Не спорь с покупателем и не обвиняй его.
- Не проси персональные данные (номер телефона, email, номер заказа) в публичном ответе.
- Для положительного отзыва: поблагодари и упомяни одну конкретную деталь из отзыва, без чрезмерного восторга.
- Для негативного отзыва: прояви участие, не спорь, не признавай неподтверждённую вину, предложи безопасный следующий шаг (например, обратиться в поддержку), не давай невыполнимых обещаний.
- Если оценка есть, а текста нет — дай короткий ответ, учитывающий саму оценку.
- Ответ не должен быть длиннее, чем того требует ситуация.
"""

JSON_CONTRACT = """\
Верни ТОЛЬКО валидный JSON без markdown-разметки, без пояснений вне JSON, строго такой формы:
{
  "sentiment": "positive | neutral | negative",
  "category": "quality | size | assembly | delivery | packaging | color | price | missing_parts | usability | other",
  "urgency": "low | medium | high",
  "reply_needed": true,
  "reply_text": "ответ покупателю на русском языке, соответствующий правилам выше",
  "advantages": ["конкретное преимущество, упомянутое в отзыве"],
  "complaints": ["конкретная жалоба, упомянутая в отзыве"],
  "product_improvements": ["рекомендация по улучшению товара, если применимо"],
  "card_improvements": ["рекомендация по улучшению карточки товара, если применимо"],
  "hypotheses": ["гипотеза, требующая проверки человеком — не выдавай её за факт"]
}
Если предположение не подтверждено текстом отзыва — помести его в "hypotheses", а не в "complaints" или "advantages".
"""


def build_store_context(settings: StoreAISettings | None) -> str:
    if not settings:
        return "Настройки магазина не заданы — используй нейтральный вежливый стиль."
    lines = [
        f"Название магазина/бренда: {settings.brand_name or 'не указано'}",
        f"Стиль общения: {settings.tone_of_voice}",
        f"Обращение к покупателю: {settings.customer_address_form}",
        f"Желаемая длина ответа: {settings.reply_length}",
        f"Использовать эмодзи: {'да, умеренно' if settings.use_emoji else 'нет'}",
    ]
    if settings.signature:
        lines.append(f"Подпись в конце ответа: {settings.signature}")
    if settings.forbidden_words:
        lines.append(f"Запрещённые слова/фразы (не использовать): {settings.forbidden_words}")
    if settings.allowed_promises:
        lines.append(f"Разрешённые обещания: {settings.allowed_promises}")
    if settings.negative_review_rules:
        lines.append(f"Доп. правила ответа на негатив: {settings.negative_review_rules}")
    if settings.warranty_info:
        lines.append(f"Информация о гарантии: {settings.warranty_info}")
    if settings.return_policy_info:
        lines.append(f"Информация о возвратах: {settings.return_policy_info}")
    if settings.support_contacts:
        lines.append(f"Контакты поддержки (можно упомянуть, не запрашивать личные данные покупателя): {settings.support_contacts}")
    if settings.product_facts:
        lines.append(f"Проверенные факты о товаре (не выходи за их рамки): {settings.product_facts}")
    return "\n".join(lines)


def build_analyze_prompt(
    *,
    product_name: str | None,
    rating: int,
    text: str | None,
    pros: str | None,
    cons: str | None,
    store_settings: StoreAISettings | None,
) -> str:
    return f"""\
Ты — ассистент службы поддержки интернет-магазина на маркетплейсе Ozon.
Проанализируй отзыв покупателя и подготовь черновик ответа.

Товар: {product_name or "не указан"}
Оценка покупателя: {rating} из 5
Текст отзыва: {text or "(текста нет, только оценка)"}
Указанные покупателем достоинства: {pros or "не указаны"}
Указанные покупателем недостатки: {cons or "не указаны"}

Настройки магазина:
{build_store_context(store_settings)}

{REPLY_RULES}
{JSON_CONTRACT}
"""


def build_repair_prompt(broken_output: str) -> str:
    return f"""\
Предыдущий ответ не является валидным JSON нужного формата. Вот он:
---
{broken_output}
---
Верни ИСПРАВЛЕННЫЙ ответ, СТРОГО в виде валидного JSON того же формата, без какого-либо текста вне JSON.
{JSON_CONTRACT}
"""


def build_rewrite_prompt(existing_reply: str, instruction: str, store_settings: StoreAISettings | None) -> str:
    instruction_text = {
        "shorter": "Сделай ответ короче, сохранив смысл и вежливость.",
        "warmer": "Сделай ответ теплее и человечнее, не переходя в чрезмерный восторг.",
        "formal": "Сделай ответ более официальным и сдержанным по тону.",
        "regenerate": "Перепиши ответ заново, другими словами, сохранив суть.",
    }.get(instruction, "Улучши формулировку ответа.")

    return f"""\
Вот текущий черновик ответа покупателю на Ozon:
---
{existing_reply}
---
Задача: {instruction_text}

Настройки магазина:
{build_store_context(store_settings)}

{REPLY_RULES}
Верни ТОЛЬКО новый текст ответа, без пояснений и без JSON.
"""
