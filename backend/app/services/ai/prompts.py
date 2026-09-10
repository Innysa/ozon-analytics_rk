"""Prompt construction shared by every AIProvider implementation.

Keeping the rules text in one place means every provider (Yandex today,
GigaChat/OpenAI/etc later) behaves consistently, since the business rules
live here rather than duplicated per-provider.
"""
from __future__ import annotations

from datetime import date

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
  "product_improvements": ["рекомендация по улучшению самого товара (материал, комплектация, инструкция и т.п.), если применимо"],
  "card_improvements": ["рекомендация по улучшению текста/структуры карточки товара (описание, характеристики, размерная сетка и т.п.), если применимо"],
  "infographic_ideas": ["конкретная идея для инфографики на фото карточки — короткий текст или визуальный акцент, отвечающий именно на то, что покупатель написал в этом отзыве (например, крупная подпись на фото с реальным размером/весом, если покупатель жаловался, что не понял размер до покупки), если применимо"],
  "hypotheses": ["гипотеза, требующая проверки человеком — не выдавай её за факт"]
}
"card_improvements" — про ТЕКСТ/структуру карточки, "infographic_ideas" — про ВИЗУАЛЬНОЕ решение на фото; не дублируй одну и ту же мысль в обоих полях.
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


ADVERTISING_JSON_CONTRACT = """\
Верни ТОЛЬКО валидный JSON без markdown-разметки, без пояснений вне JSON, строго такой формы:
{
  "overview": "2-4 предложения — общая картина по рекламе магазина за период",
  "insights": [
    {"ozon_campaign_id": "...", "campaign_name": "...", "assessment": "strong | weak | neutral", "note": "1-2 предложения, почему"}
  ],
  "anomalies": ["конкретная аномалия или тренд, например «резкий рост расхода без роста кликов у кампании X»"],
  "recommendations": ["конкретная рекомендация по действию"]
}
Заказы, ДРР и ROAS в данных ниже отсутствуют — не упоминай их и не пытайся оценить рентабельность кампаний,
опирайся только на расход, показы, клики и CTR.
"""


def _format_campaign_for_prompt(c: dict) -> str:
    daily_lines = "\n".join(
        f"    {d['date']}: расход {d['spend_rub']} ₽, показы {d['impressions']}, клики {d['clicks']}"
        for d in c.get("daily", [])
    )
    return f"""\
- {c['name']} (ozon_campaign_id={c['ozon_campaign_id']}, тип={c.get('campaign_type') or 'не указан'}, \
статус={c.get('state') or 'не указан'}, дневной бюджет={c.get('daily_budget_rub') if c.get('daily_budget_rub') is not None else 'не указан'})
  Итого за период: расход {c['total_spend_rub']} ₽, показы {c['total_impressions']}, клики {c['total_clicks']}, \
CTR {c['ctr_pct'] if c['ctr_pct'] is not None else 'нет данных'}%
  По дням:
{daily_lines or '    нет данных по дням'}"""


def build_advertising_analysis_prompt(
    *,
    store_name: str | None,
    period_start: date,
    period_end: date,
    campaigns: list[dict],
) -> str:
    campaigns_text = "\n".join(_format_campaign_for_prompt(c) for c in campaigns)
    return f"""\
Ты — аналитик по рекламе на маркетплейсе Ozon. Магазин: {store_name or "не указан"}.
Проанализируй автоматически собранную статистику рекламных кампаний за период {period_start.isoformat()} — {period_end.isoformat()}
(источник — Ozon Performance API, показатели: расход, показы, клики, CTR; заказы и ДРР пока не собираются отдельно и в данных ниже отсутствуют).

Кампании:
{campaigns_text}

Задача: определи, какие кампании выглядят сильными, какие слабыми, и заметь аномалии/тренды —
например резкий рост расхода без роста кликов, падающий CTR, кампанию с нулевыми показами при ненулевом бюджете и т.п.
Не оценивай рентабельность и не упоминай ДРР/ROAS/заказы — этих данных нет.

{ADVERTISING_JSON_CONTRACT}
"""


def build_advertising_repair_prompt(broken_output: str) -> str:
    return f"""\
Предыдущий ответ не является валидным JSON нужного формата. Вот он:
---
{broken_output}
---
Верни ИСПРАВЛЕННЫЙ ответ, СТРОГО в виде валидного JSON того же формата, без какого-либо текста вне JSON.
{ADVERTISING_JSON_CONTRACT}
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
