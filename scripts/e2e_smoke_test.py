"""Manual end-to-end smoke test for the reviews module, run against a live
backend (not mocks): CSV upload -> AI analyze -> draft generation -> human
edit -> the mandatory approval gate -> publish (graceful degradation without
real Ozon credentials) -> copy fallback, plus store-isolation checks across
two independent, non-admin users and their two separate stores.

This is a manual verification tool, not part of the pytest suite (see
tests/test_store_isolation.py and tests/test_reviews.py for the automated,
DB-transaction-isolated equivalents) — use it to sanity-check a real running
instance end-to-end, e.g. after infrastructure changes or before a deploy.

Usage:
    # 1) Start Postgres and apply migrations, then start the API, e.g.:
    #    export DATABASE_URL=... APP_ENCRYPTION_KEY=... SESSION_SECRET=...
    #    export AI_PROVIDER=demo DEMO_MODE=true
    #    alembic -c backend/alembic.ini upgrade head
    #    cd backend && python -m app.seed.bootstrap_admin admin@example.com Admin adminpass123
    #    uvicorn app.main:app --port 8000 &
    # 2) Run this script against a database you don't mind seeding test data into:
    #    E2E_ADMIN_EMAIL=admin@example.com E2E_ADMIN_PASSWORD=adminpass123 \
    #        python scripts/e2e_smoke_test.py

Only needs `httpx`, which is already a backend dependency (see
backend/requirements.txt) — no extra packages required.

Prints PASS/FAIL for every check and exits non-zero if anything failed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

import httpx

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8000/api")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "adminpass123")

# Unique suffix so this script can be re-run against the same database
# without colliding on the unique user-email constraint.
RUN_ID = uuid.uuid4().hex[:8]

FAILS: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label} {extra}")
    if not cond:
        FAILS.append(label)


def session() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=30.0)


def login(s: httpx.Client, email: str, password: str) -> httpx.Response:
    r = s.post("/auth/login", json={"email": email, "password": password})
    check(f"login {email}", r.status_code == 200, r.text[:200])
    return r


def write_temp_csv(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="e2e_reviews_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def main() -> int:
    admin = session()
    login(admin, ADMIN_EMAIL, ADMIN_PASSWORD)

    # --- Admin: create two stores ---
    r = admin.post("/stores", json={"name": f"Магазин Восток {RUN_ID}"})
    check("create store A", r.status_code == 201, r.text[:200])
    store_a = r.json()["id"]

    r = admin.post("/stores", json={"name": f"Магазин Запад {RUN_ID}"})
    check("create store B", r.status_code == 201, r.text[:200])
    store_b = r.json()["id"]

    # --- Admin: create two non-admin managers, one per store ---
    r = admin.post("/users", json={"email": f"manager_a_{RUN_ID}@example.com", "full_name": "Manager A", "password": "password123"})
    check("create manager_a", r.status_code == 201, r.text[:200])
    manager_a_id = r.json()["id"]

    r = admin.post("/users", json={"email": f"manager_b_{RUN_ID}@example.com", "full_name": "Manager B", "password": "password123"})
    check("create manager_b", r.status_code == 201, r.text[:200])
    manager_b_id = r.json()["id"]

    r = admin.post("/users/memberships", json={"user_id": manager_a_id, "store_id": store_a, "role": "manager"})
    check("assign manager_a -> store A", r.status_code == 201 and r.json()["role"] == "manager")

    r = admin.post("/users/memberships", json={"user_id": manager_b_id, "store_id": store_b, "role": "manager"})
    check("assign manager_b -> store B", r.status_code == 201 and r.json()["role"] == "manager")

    # Also create a viewer on store A to test read-only restriction
    r = admin.post("/users", json={"email": f"viewer_a_{RUN_ID}@example.com", "full_name": "Viewer A", "password": "password123"})
    viewer_a_id = r.json()["id"]
    r = admin.post("/users/memberships", json={"user_id": viewer_a_id, "store_id": store_a, "role": "viewer"})
    check("assign viewer_a -> store A", r.status_code == 201)

    # --- manager_a session ---
    mgr_a = session()
    login(mgr_a, f"manager_a_{RUN_ID}@example.com", "password123")

    r = mgr_a.get("/stores")
    ids = {s["id"] for s in r.json()}
    check("manager_a sees only store A in /stores", ids == {store_a}, f"got {ids}")

    # --- manager_b session ---
    mgr_b = session()
    login(mgr_b, f"manager_b_{RUN_ID}@example.com", "password123")

    r = mgr_b.get("/stores")
    ids = {s["id"] for s in r.json()}
    check("manager_b sees only store B in /stores", ids == {store_b}, f"got {ids}")

    # ============ UPLOAD CSV (store A, realistic mixed-rating data) ============
    csv_content = (
        "ozon_review_id,sku,offer_id,product_name,rating,text,pros,cons,published_at\n"
        f"e2e-{RUN_ID}-r1,SKU-1001,ART-1001,Термокружка Alpha 500мл,5,"
        "\"Отличная кружка, держит тепло весь рабочий день, беру с собой каждый день\","
        "\"держит тепло,удобная крышка\",,2026-08-01\n"
        f"e2e-{RUN_ID}-r2,SKU-1001,ART-1001,Термокружка Alpha 500мл,2,"
        "\"Крышка треснула через неделю использования, очень разочарован\",,"
        "\"крышка треснула,хлипкий пластик\",2026-08-10\n"
        f"e2e-{RUN_ID}-r3,SKU-1002,ART-1002,Рюкзак городской Beta,4,"
        "\"Удобный рюкзак, но молния тугая и цепляется за ткань\",удобный,"
        "\"молния тугая\",2026-08-15\n"
        f"e2e-{RUN_ID}-r4,SKU-1002,ART-1002,Рюкзак городской Beta,1,"
        "\"Пришёл с браком, шов разошёлся сразу же, требую вернуть деньги\",,"
        "\"брак,шов разошёлся\",2026-08-20\n"
        f"e2e-{RUN_ID}-r5,SKU-1003,ART-1003,Наушники Gamma,3,,,,2026-08-22\n"
    )
    csv_path_a = write_temp_csv(csv_content)

    with open(csv_path_a, "rb") as f:
        r = mgr_a.post(f"/stores/{store_a}/reviews/upload", files={"file": ("e2e_reviews.csv", f, "text/csv")})
    check("upload CSV to store A", r.status_code == 200, r.text[:300])
    upload_result = r.json()
    check("upload created 5 reviews", upload_result["created"] == 5, str(upload_result))

    # Re-upload same file -> must be deduped, not duplicated
    with open(csv_path_a, "rb") as f:
        r = mgr_a.post(f"/stores/{store_a}/reviews/upload", files={"file": ("e2e_reviews.csv", f, "text/csv")})
    dup_result = r.json()
    check("re-upload dedups all 5 as duplicates", dup_result["skipped_duplicate"] == 5 and dup_result["created"] == 0, str(dup_result))

    # manager_b uploads a different review set into store B (to test cross leakage later)
    csv_content_b = (
        "ozon_review_id,sku,product_name,rating,text\n"
        f'e2e-{RUN_ID}-b1,SKU-9001,Товар магазина Запад,5,"Прекрасный товар, все понравилось"\n'
    )
    csv_path_b = write_temp_csv(csv_content_b)
    with open(csv_path_b, "rb") as f:
        r = mgr_b.post(f"/stores/{store_b}/reviews/upload", files={"file": ("e2e_reviews_b.csv", f, "text/csv")})
    check("upload CSV to store B", r.status_code == 200 and r.json()["created"] == 1, r.text[:300])

    # ============ LIST REVIEWS (store A) ============
    r = mgr_a.get(f"/stores/{store_a}/reviews")
    check("list reviews store A", r.status_code == 200)
    reviews_a = r.json()["items"]
    check("store A has exactly 5 reviews visible", len(reviews_a) == 5, f"got {len(reviews_a)}")
    expected_ids = {f"e2e-{RUN_ID}-r{i}" for i in range(1, 6)}
    review_ids_a = {rv["ozon_review_id"] for rv in reviews_a}
    check("store A review set matches uploaded IDs, no store B leakage", review_ids_a == expected_ids, str(review_ids_a))

    negative_review = next(rv for rv in reviews_a if rv["ozon_review_id"] == f"e2e-{RUN_ID}-r2")
    positive_review = next(rv for rv in reviews_a if rv["ozon_review_id"] == f"e2e-{RUN_ID}-r1")
    no_text_review = next(rv for rv in reviews_a if rv["ozon_review_id"] == f"e2e-{RUN_ID}-r5")

    # ============ ANALYZE ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/analyze")
    check("analyze negative review (2 stars, broken lid)", r.status_code == 200, r.text[:300])
    analyzed = r.json()
    check("analysis sentiment == negative", analyzed["analysis"]["sentiment"] == "negative", str(analyzed["analysis"]))
    check("analysis urgency == high", analyzed["analysis"]["urgency"] == "high")
    check("review status == analyzed", analyzed["status"] == "analyzed")

    r = mgr_a.post(f"/stores/{store_a}/reviews/{positive_review['id']}/analyze")
    analyzed_pos = r.json()
    check("analyze positive review -> sentiment positive", analyzed_pos["analysis"]["sentiment"] == "positive", str(analyzed_pos.get("analysis")))

    # no-text, rating-only review must still get a short, rating-aware analysis
    r = mgr_a.post(f"/stores/{store_a}/reviews/{no_text_review['id']}/analyze")
    check("analyze rating-only review (3 stars, no text) succeeds", r.status_code == 200, r.text[:300])

    # ============ GENERATE DRAFT (AI) ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/generate-draft")
    check("generate-draft for negative review", r.status_code == 200, r.text[:300])
    draft_review = r.json()
    draft = draft_review["latest_draft"]
    check("draft created, status draft_created", draft_review["status"] == "draft_created" and draft["status"] == "draft")
    check("draft is AI-generated", draft["generated_by_ai"] is True)
    check("draft not edited yet", draft["edited_by_user"] is False)
    comment_id = draft["id"]

    # ============ REWRITE variants ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/rewrite", json={"instruction": "warmer"})
    check("rewrite draft (warmer)", r.status_code == 200, r.text[:300])

    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/rewrite", json={"instruction": "shorter"})
    check("rewrite draft (shorter)", r.status_code == 200, r.text[:300])

    # ============ HUMAN EDIT ============
    edited_text = "Спасибо за отзыв. Нам жаль, что крышка вышла из строя — напишите нам в поддержку, разберёмся."
    r = mgr_a.patch(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}", json={"text": edited_text})
    check("human edits draft text", r.status_code == 200 and r.json()["edited_by_user"] is True, r.text[:300])
    check("edited text persisted", r.json()["text"] == edited_text)

    # viewer must NOT be able to approve/publish
    viewer_a = session()
    login(viewer_a, f"viewer_a_{RUN_ID}@example.com", "password123")
    r = viewer_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/approve")
    check("viewer_a cannot approve (403)", r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")

    # ============ PUBLISH BEFORE APPROVAL MUST BE BLOCKED ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/publish")
    check("publish blocked before approval (400)", r.status_code == 400, f"got {r.status_code}: {r.text[:200]}")

    # ============ HUMAN APPROVAL (the mandatory gate) ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/approve")
    check("approve draft", r.status_code == 200 and r.json()["status"] == "approved", r.text[:300])

    # Editing after approval must now be blocked
    r = mgr_a.patch(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}", json={"text": "изменённый текст"})
    check("editing after approval is blocked (400)", r.status_code == 400, f"got {r.status_code}")

    # ============ PUBLISH: no real Ozon credentials -> graceful degradation ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/publish")
    check("publish without Ozon creds fails gracefully (409, not 500)", r.status_code == 409, f"got {r.status_code}: {r.text[:200]}")
    check("publish error message suggests manual copy", "скопир" in r.json().get("detail", "").lower(), r.text[:300])

    # ============ COPY (manual fallback) ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/copy")
    check("copy approved reply for manual publication", r.status_code == 200, r.text[:300])

    # ============ ROLE BOUNDARY: manager (not owner) cannot manage Ozon credentials ============
    r = mgr_a.put(f"/stores/{store_a}/ozon/credentials", json={"client_id": "12345", "api_key": "fake-key-not-real"})
    check("manager_a (role=manager) CANNOT set Ozon credentials -> 403 (owner-only)", r.status_code == 403, r.text[:300])

    # ============ OZON CONNECTION CHECK (invalid creds -> graceful, not crash) ============
    # credentials management is owner/admin-only; use the admin session (which also
    # proves admin can operate on store A despite having no explicit membership row)
    r = admin.put(f"/stores/{store_a}/ozon/credentials", json={"client_id": "12345", "api_key": "fake-key-not-real"})
    check("admin saves (fake) Ozon credentials for store A", r.status_code == 200, r.text[:300])
    check(
        "api key masked in response, full value never returned",
        r.json()["api_key_masked"] != "fake-key-not-real" and "fake-key-not-real" not in r.text,
        str(r.json()),
    )

    r = admin.post(f"/stores/{store_a}/ozon/check-connection")
    check("check-connection with fake creds returns 200 (no crash)", r.status_code == 200, r.text[:300])
    check("check-connection reports failure clearly", r.json().get("last_connection_ok") is False, str(r.json()))

    # Publish should still degrade gracefully after a failed connection check, not crash
    r = mgr_a.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/publish")
    check("publish still fails gracefully after failed connection check", r.status_code in (409, 502), f"got {r.status_code}: {r.text[:200]}")

    # ============ NO-REPLY-NEEDED path ============
    r = mgr_a.post(f"/stores/{store_a}/reviews/{positive_review['id']}/no-reply-needed")
    check("mark positive review no-reply-needed", r.status_code == 200 and r.json()["status"] == "no_reply_needed", r.text[:300])

    # ============ BULK DRAFT GENERATION (bulk publish must not exist) ============
    r = mgr_a.post(
        f"/stores/{store_a}/reviews/bulk/generate-drafts",
        json=[rv["id"] for rv in reviews_a if rv["id"] not in (negative_review["id"], positive_review["id"])],
    )
    check("bulk generate-drafts succeeds", r.status_code == 200, r.text[:300])
    bulk_result = r.json()
    check("bulk generated drafts for remaining reviews", bulk_result["succeeded"] >= 2, str(bulk_result))

    # confirm there is truly no bulk-publish endpoint (404 normally; 405 if the SPA
    # catch-all route is registered, since that matches any path on GET only —
    # either way, no handler exists that would perform a bulk publish)
    r = mgr_a.post(f"/stores/{store_a}/reviews/bulk/publish")
    check("no bulk-publish endpoint exists (404/405, not 200)", r.status_code in (404, 405), f"got {r.status_code}")

    # ============ ANALYTICS (facts vs hypotheses separated, evidence review_ids present) ============
    r = mgr_a.get(f"/stores/{store_a}/analytics/reviews")
    check("analytics endpoint returns data for store A", r.status_code == 200 and r.json()["has_data"] is True, r.text[:300])
    analytics_a = r.json()
    check("analytics total_reviews == 5", analytics_a["total_reviews"] == 5, str(analytics_a["total_reviews"]))
    check("analytics complaints carry evidence review_ids", all(len(c["review_ids"]) > 0 for c in analytics_a["top_complaints"]), str(analytics_a["top_complaints"]))

    # ====================================================================
    # STORE ISOLATION: manager_b (store B only) must NOT reach store A data
    # ====================================================================
    r = mgr_b.get(f"/stores/{store_a}/reviews")
    check("ISOLATION: manager_b GET store A reviews -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/reviews/{negative_review['id']}")
    check("ISOLATION: manager_b GET single store-A review by real ID -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.post(f"/stores/{store_a}/reviews/{positive_review['id']}/analyze")
    check("ISOLATION: manager_b cannot analyze store-A review -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/approve")
    check("ISOLATION: manager_b cannot approve store-A comment -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.post(f"/stores/{store_a}/reviews/{negative_review['id']}/comments/{comment_id}/publish")
    check("ISOLATION: manager_b cannot publish store-A comment -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/analytics/reviews")
    check("ISOLATION: manager_b cannot read store-A analytics -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/ai-settings")
    check("ISOLATION: manager_b cannot read store-A AI settings -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/ozon/credentials")
    check("ISOLATION: manager_b cannot read store-A Ozon credentials -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/products")
    check("ISOLATION: manager_b cannot list store-A products -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/sync/runs")
    check("ISOLATION: manager_b cannot read store-A sync runs -> 403", r.status_code == 403, f"got {r.status_code}")

    r = mgr_b.get(f"/stores/{store_a}/change-history")
    check("ISOLATION: manager_b cannot read store-A change-history -> 403", r.status_code == 403, f"got {r.status_code}")

    with open(csv_path_a, "rb") as f:
        r = mgr_b.post(f"/stores/{store_a}/reviews/upload", files={"file": ("x.csv", f, "text/csv")})
    check("ISOLATION: manager_b cannot upload into store A -> 403", r.status_code == 403, f"got {r.status_code}")

    # Reverse direction: manager_a must not reach store B
    r = mgr_a.get(f"/stores/{store_b}/reviews")
    check("ISOLATION: manager_a GET store B reviews -> 403", r.status_code == 403, f"got {r.status_code}")

    # manager_a's own review list must never contain store B's review even though
    # both stores share nothing but the same review-list endpoint shape
    r = mgr_a.get(f"/stores/{store_a}/reviews")
    ids_after = {rv["ozon_review_id"] for rv in r.json()["items"]}
    check("store A review list still has no store-B review id (no cross-store bleed)", f"e2e-{RUN_ID}-b1" not in ids_after, str(ids_after))

    r = mgr_b.get(f"/stores/{store_b}/reviews")
    ids_b = {rv["ozon_review_id"] for rv in r.json()["items"]}
    check("store B review list contains only its own review", ids_b == {f"e2e-{RUN_ID}-b1"}, str(ids_b))

    # Unauthenticated access must be rejected outright
    anon = session()
    r = anon.get(f"/stores/{store_a}/reviews")
    check("ISOLATION: unauthenticated request -> 401", r.status_code == 401, f"got {r.status_code}")

    # Admin, by contrast, must be able to reach both stores
    r = admin.get(f"/stores/{store_a}/reviews")
    check("admin CAN read store A reviews", r.status_code == 200)
    r = admin.get(f"/stores/{store_b}/reviews")
    check("admin CAN read store B reviews", r.status_code == 200)

    os.remove(csv_path_a)
    os.remove(csv_path_b)

    print()
    print("=" * 60)
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
