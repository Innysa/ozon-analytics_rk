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


# ИСПРАВЛЕНО 2026-09-22: реальный магазин с 60+ кампаниями получал insights
# всего на ~3 из них — модель просто выбирала несколько показательных
# примеров вместо разбора каждой. Теперь contract явно требует ровно одну
# запись на КАЖДУЮ кампанию из блока «Итого по кампаниям» ниже (id оттуда,
# один в один) — неполный список больше не проходит как «готовый ответ».
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
ВАЖНО: "insights" должен содержать РОВНО ОДНУ запись на КАЖДУЮ кампанию из блока «Итого по кампаниям» ниже —
ни одна не должна быть пропущена, даже слабая или без заметных изменений (note может быть коротким, например
«без особенностей за период»). Используй ozon_campaign_id ТОЧНО как в списке. Не добавляй кампании, которых
там нет.
Заказы, ДРР и ROAS в данных ниже отсутствуют — не упоминай их и не пытайся оценить рентабельность кампаний,
опирайся только на расход, показы, клики и CTR.
"""

# Полная раскладка по дням присылается только для этого числа кампаний с
# наибольшим расходом (список уже отсортирован по расходу, см.
# _aggregate_campaigns) — 60+ кампаний × 2 недели по дням раздули бы запрос
# до неприемлемого размера почти без пользы: у мелких кампаний по расходу
# и так мало данных для тренда. Остальные попадают только в компактный
# однострочный «Итого по кампаниям» — ЕГО модель обязана покрыть целиком
# (см. ADVERTISING_JSON_CONTRACT выше).
CAMPAIGNS_WITH_DAILY_DETAIL = 15


def _format_campaign_totals_line(c: dict) -> str:
    return (
        f"- id={c['ozon_campaign_id']} | {c['name']} | тип={c.get('campaign_type') or 'не указан'} | "
        f"статус={c.get('state') or 'не указан'} | расход {c['total_spend_rub']} ₽ | показы {c['total_impressions']} | "
        f"клики {c['total_clicks']} | CTR {c['ctr_pct'] if c['ctr_pct'] is not None else 'нет данных'}%"
    )


def _format_campaign_with_daily(c: dict) -> str:
    daily_lines = "\n".join(
        f"    {d['date']}: расход {d['spend_rub']} ₽, показы {d['impressions']}, клики {d['clicks']}"
        for d in c.get("daily", [])
    )
    return f"""\
- {c['name']} (ozon_campaign_id={c['ozon_campaign_id']})
  По дням:
{daily_lines or '    нет данных по дням'}"""


def build_advertising_analysis_prompt(
    *,
    store_name: str | None,
    period_start: date,
    period_end: date,
    campaigns: list[dict],
) -> str:
    # campaigns is already sorted by total_spend_rub descending (see
    # _aggregate_campaigns) — the top N get full daily detail for
    # trend-spotting, everyone else still gets a totals line, and
    # ADVERTISING_JSON_CONTRACT requires an insight for every single one.
    totals_text = "\n".join(_format_campaign_totals_line(c) for c in campaigns)
    daily_detail_text = "\n".join(_format_campaign_with_daily(c) for c in campaigns[:CAMPAIGNS_WITH_DAILY_DETAIL])
    return f"""\
Ты — аналитик по рекламе на маркетплейсе Ozon. Магазин: {store_name or "не указан"}.
Проанализируй автоматически собранную статистику рекламных кампаний за период {period_start.isoformat()} — {period_end.isoformat()}
(источник — Ozon Performance API, показатели: расход, показы, клики, CTR; заказы и ДРР пока не собираются отдельно и в данных ниже отсутствуют).

Итого по кампаниям за период (все {len(campaigns)}, отсортированы по расходу):
{totals_text}

Раскладка по дням для {min(CAMPAIGNS_WITH_DAILY_DETAIL, len(campaigns))} кампаний с наибольшим расходом (для трендов/аномалий):
{daily_detail_text}

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
