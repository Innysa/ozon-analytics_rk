"""Tests for the Ozon Performance API statistics-report ZIP/CSV parser
(app.services.advertising_daily_statistic_parser), against synthetic fixtures
matching the real column layout/encoding/labels: ';'-delimited CSV, a title
line, a header line, data rows, and a final "Всего" totals row; campaign id
taken from the filename, not a column; encoding is either UTF-8-with-BOM or
Windows-1251. The orders/revenue/ДРР column labels below ("Продано товаров",
"Продажи в продвижении, ₽", "ДРР в продвижении, %", "ДРР (общий), %") are
the ones a real account's raw_payload confirmed — an earlier version of this
fixture used guessed labels ("Заказы"/"Выручка, ₽") that don't match what
Ozon actually returns for this report; see the parser's own module docstring
for the full story."""
import io
import zipfile
from datetime import date

from app.services.advertising_daily_statistic_parser import parse_statistics_report_zip

_HEADER = (
    "SKU;Название товара;День;Цена товара;Тип страницы;Условие показа;Показы;Клики;"
    "CTR (%);В корзину;Средняя ставка (руб.);Расход, ₽ с НДС;Продано товаров;"
    "Продажи в продвижении, ₽;ДРР в продвижении, %;ДРР (общий), %"
)


def _make_csv(rows: list[str], title: str = "Статистика по кампании; 01.09.2026-03.09.2026") -> str:
    return "\n".join([title, _HEADER, *rows])


def _zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_parses_data_rows_and_skips_totals_row():
    # Row1's orders/revenue/ДРР values match the real example a live account
    # confirmed (via raw_payload, see this parser's module docstring): 1
    # order, 2750,00 ₽, 4,1% — the exact case that caught the original
    # "заказы"/"выручка" guessed-column bug (both always None before this).
    csv_text = _make_csv(
        [
            "12345;Товар А;01.09.2026;1500;PDP;search;1000;50;5,0;10;12,50;625,40;1;2750,00;4,1;5,0",
            "12345;Товар А;02.09.2026;1500;PDP;search;1100;60;5,45;12;12,80;767,00;4;6000,00;3,5;4,0",
            "Всего;;;;;;2100;110;5,24;22;;1392,40;5;8750,00;;",
        ]
    )
    zip_bytes = _zip_of({"111_01.09.2026-03.09.2026.csv": csv_text.encode("utf-8-sig")})

    result = parse_statistics_report_zip(zip_bytes)

    assert result.warnings == []
    assert len(result.rows) == 2
    row1 = result.rows[0]
    assert row1["ozon_campaign_id"] == "111"
    assert row1["sku"] == "12345"
    assert row1["date"] == date(2026, 9, 1)
    assert row1["impressions"] == 1000
    assert row1["clicks"] == 50
    assert row1["ctr_pct_ozon"] == 5.0
    assert row1["spend_rub"] == 625.40
    assert row1["orders"] == 1
    assert row1["revenue_rub"] == 2750.00
    assert row1["drr_promo_pct_ozon"] == 4.1
    assert row1["drr_total_pct_ozon"] == 5.0


def test_campaign_with_zero_data_still_parses_zero_rows_not_an_error():
    """A campaign with genuinely zero impressions for the whole period still
    gets a fully-zero data row plus a zero totals row — neither is an error,
    per the ТЗ's explicit requirement that zero-data campaigns must not break
    parsing."""
    csv_text = _make_csv(
        [
            "99999;Товар Б;01.09.2026;900;PDP;search;0;0;0;0;0;0;0;0;0;0",
            "Всего;;;;;;0;0;0;0;;0;0;0;0;0",
        ]
    )
    zip_bytes = _zip_of({"222_01.09.2026-01.09.2026.csv": csv_text.encode("utf-8-sig")})

    result = parse_statistics_report_zip(zip_bytes)

    assert result.warnings == []
    assert len(result.rows) == 1
    assert result.rows[0]["impressions"] == 0
    assert result.rows[0]["spend_rub"] == 0


def test_handles_cp1251_encoding_fallback():
    # Windows-1251 (the codec Python ships) predates the 2014 update that
    # added the ₽ glyph to that code page, so a header containing it can't
    # round-trip through cp1251 — use "руб." (spelled out) instead, same as
    # real cp1251-encoded Ozon exports do for this column.
    header = _HEADER.replace("Расход, ₽ с НДС", "Расход руб. с НДС").replace(
        "Продажи в продвижении, ₽", "Продажи в продвижении руб."
    )
    csv_text = "\n".join(["Статистика по кампании; 01.09.2026-03.09.2026", header, "55555;Товар В;03.09.2026;500;PDP;search;10;1;10;1;5,00;5,00;0;0;0;0"])
    zip_bytes = _zip_of({"333_03.09.2026-03.09.2026.csv": csv_text.encode("cp1251")})

    result = parse_statistics_report_zip(zip_bytes)

    assert result.warnings == []
    assert len(result.rows) == 1
    assert result.rows[0]["sku"] == "55555"
    assert result.rows[0]["product_name"] == "Товар В"


def test_multiple_campaign_csvs_in_one_zip_are_all_parsed():
    csv_a = _make_csv(["1;Товар A;01.09.2026;100;PDP;search;10;1;10;1;1,00;1,00;0;0;0;0"])
    csv_b = _make_csv(["2;Товар B;01.09.2026;200;PDP;search;20;2;10;2;2,00;2,00;0;0;0;0"])
    zip_bytes = _zip_of(
        {
            "111_01.09.2026-01.09.2026.csv": csv_a.encode("utf-8-sig"),
            "222_01.09.2026-01.09.2026.csv": csv_b.encode("utf-8-sig"),
        }
    )

    result = parse_statistics_report_zip(zip_bytes)

    campaign_ids = {r["ozon_campaign_id"] for r in result.rows}
    assert campaign_ids == {"111", "222"}


def test_bad_zip_reports_a_warning_not_a_crash():
    result = parse_statistics_report_zip(b"not a zip file")
    assert result.rows == []
    assert len(result.warnings) == 1
