"""Tests for the manual CSV/XLSX review import (app.services.review_import).

No test existed for this importer before — that gap is exactly how the real
bug shipped unnoticed: Ozon's actual "Отзывы → Скачать отчёт" export is
';'-delimited with a UTF-8 BOM, has both "Артикул" (offer id) and "SKU"
(ozon sku) as separate columns, and has no dedicated review-id column at all
(see review_import.py's module docstring for the full story, confirmed
against a real downloaded file a user reported "uploading does nothing"
for). Every real upload failed at the parse step (default pandas assumes
comma) with zero visible effect."""
from app.models.review import Review
from app.services.review_import import import_reviews_from_file

# A representative excerpt of the real confirmed export shape: ';'-delimited,
# UTF-8 BOM, a product name containing a comma (this is what breaks a plain
# comma-delimited read outright), both Артикул and SKU columns, and no
# ID-отзыва column at all.
_REAL_EXPORT_CSV = (
    "Артикул;SKU;Название товара;Номер заказа;Статус получения;Текст отзыва;"
    "Дата публикации;Статус отзыва;Оценка;Количество фото;Количество видео;"
    "Количество ответов на отзыв\n"
    "пуф/S/сер1/2;4053047123;\"Складной пуф, размер 30*30, серый\";19088847-0982;"
    "Получен;;2026-08-31T17:06:30Z;Новый;5;0;0;0\n"
    "ковер/мех/сер/200;1492106823;Ковер на пол 80х200;0225485653-0073;Получен;"
    "Хороший ковер;2026-08-31T17:06:32Z;Новый;4;0;0;0\n"
).encode("utf-8-sig")


def test_real_ozon_export_shape_imports_successfully(db_session, two_stores_with_users):
    d = two_stores_with_users
    result = import_reviews_from_file(
        db_session, store_id=d["store_a"].id, filename="ozon_reviews.csv", content=_REAL_EXPORT_CSV
    )

    assert result.errors == []
    assert result.fetched == 2
    assert result.created == 2
    db_session.commit()

    reviews = db_session.query(Review).filter(Review.store_id == d["store_a"].id).order_by(Review.rating.desc()).all()
    assert len(reviews) == 2
    assert reviews[0].rating == 5
    assert reviews[0].ozon_review_id == "order:19088847-0982"
    assert reviews[1].text == "Хороший ковер"


def test_real_export_product_uses_sku_not_artikul_for_ozon_sku(db_session, two_stores_with_users):
    """The real export has BOTH "Артикул" (seller's own offer id, e.g.
    "пуф/S/сер1/2") and "SKU" (Ozon's numeric id) — the product created from
    it must be matched/created by the real numeric SKU, with "Артикул"
    stored separately as offer_id, not confused for the sku itself."""
    d = two_stores_with_users
    import_reviews_from_file(db_session, store_id=d["store_a"].id, filename="ozon_reviews.csv", content=_REAL_EXPORT_CSV)
    db_session.commit()

    from app.models.product import Product

    product = db_session.query(Product).filter(Product.store_id == d["store_a"].id, Product.ozon_sku == "4053047123").first()
    assert product is not None
    assert product.offer_id == "пуф/S/сер1/2"


def test_reupload_of_same_real_export_is_deduplicated_by_order_number(db_session, two_stores_with_users):
    d = two_stores_with_users
    import_reviews_from_file(db_session, store_id=d["store_a"].id, filename="ozon_reviews.csv", content=_REAL_EXPORT_CSV)
    db_session.commit()

    result2 = import_reviews_from_file(
        db_session, store_id=d["store_a"].id, filename="ozon_reviews.csv", content=_REAL_EXPORT_CSV
    )

    assert result2.created == 0
    assert result2.skipped_duplicate == 2


def test_simple_hand_built_template_with_explicit_review_id_still_works(db_session, two_stores_with_users):
    """Backward compatibility with the originally-documented simple format:
    comma-delimited, an explicit ozon_review_id column, "артикул" as the
    sole sku-like column (no separate "sku" column)."""
    d = two_stores_with_users
    csv_bytes = (
        "ozon_review_id,артикул,товар,оценка,текст\n"
        "rev-1,12345,Тестовый товар,5,Отличный товар\n"
    ).encode("utf-8")

    result = import_reviews_from_file(db_session, store_id=d["store_a"].id, filename="simple.csv", content=csv_bytes)

    assert result.errors == []
    assert result.created == 1
    db_session.commit()
    review = db_session.query(Review).filter(Review.store_id == d["store_a"].id).one()
    assert review.ozon_review_id == "rev-1"
    assert review.product.ozon_sku == "12345"


def test_missing_both_id_and_order_number_columns_is_a_clear_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    csv_bytes = "оценка,текст\n5,Хорошо\n".encode("utf-8")

    result = import_reviews_from_file(db_session, store_id=d["store_a"].id, filename="bad.csv", content=csv_bytes)

    assert result.created == 0
    assert len(result.errors) == 1
    assert "ID отзыва" in result.errors[0]
