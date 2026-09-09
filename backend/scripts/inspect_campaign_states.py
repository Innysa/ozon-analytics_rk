"""One-off diagnostic script: does NOT call Ozon — reads what the app itself
already stored in advertising_campaigns.raw_payload (the full, unfiltered JSON
Ozon sent for GET /api/client/campaign, captured via OzonCampaignItem's
extra="allow") for campaigns already synced into this store.

Written to debug the reported symptom: the "Активные" tab shows campaigns
with state == "CAMPAIGN_STATE_INACTIVE" (e.g. "апт/2+1/табл/тон ПОЛКИ")
mixed in with "CAMPAIGN_STATE_RUNNING" ones. The question: does Ozon expose a
distinct archival flag/field beyond `state` that our current filter
(state != "CAMPAIGN_STATE_ARCHIVED") isn't using?

No arguments take free text that needs shell-quoting (a browser-based server
console mangles quotes/slashes around Cyrillic text) — everything below is
selected by plain flags instead:

    # Just the counts (guaranteed short, safe to copy on its own):
    docker compose exec app python backend/scripts/inspect_campaign_states.py --store-id <id> --counts-only

    # Full diagnostic: counts + the union of all JSON keys ever seen + one
    # compact example per distinct state (a handful of likely-relevant
    # fields only, not the full ~30-field payload):
    docker compose exec app python backend/scripts/inspect_campaign_states.py --store-id <id>

    # All campaigns currently in a given state (name + id + the same compact
    # field subset) — use this instead of searching by name to find a
    # specific campaign, e.g. every INACTIVE one:
    docker compose exec app python backend/scripts/inspect_campaign_states.py --store-id <id> --state CAMPAIGN_STATE_INACTIVE
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.advertising_campaign import AdvertisingCampaign  # noqa: E402

# Fields worth showing per example without dumping the entire ~30-key
# payload — state itself, anything that looks archive-related by name, and a
# few fields that plausibly gate visibility/lifecycle.
_INTERESTING_KEYS = [
    "state",
    "autostopStatus",
    "createdAt",
    "updatedAt",
    "fromDate",
    "toDate",
]


def _print_counts(campaigns: list[AdvertisingCampaign]) -> None:
    print(f"Всего кампаний в базе: {len(campaigns)}")
    print("=== Распределение по state ===")
    counts = Counter(c.state for c in campaigns)
    for state, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {state!r}: {n}")


def _compact_fields(raw_payload: str | None) -> dict:
    if not raw_payload:
        return {}
    data = json.loads(raw_payload)
    result = {k: data.get(k) for k in _INTERESTING_KEYS if k in data}
    archive_like = {k: v for k, v in data.items() if "archiv" in k.lower()}
    result.update(archive_like)
    return result


def _print_campaign_compact(c: AdvertisingCampaign) -> None:
    fields = _compact_fields(c.raw_payload)
    print(f"  id={c.ozon_campaign_id} name={c.name!r}")
    print(f"    {fields!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--counts-only", action="store_true", help="Print only the state distribution and stop")
    parser.add_argument(
        "--state", default=None,
        help="Print every campaign currently at this exact state value (e.g. CAMPAIGN_STATE_INACTIVE)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        campaigns = db.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == args.store_id).all()
        if not campaigns:
            print("Нет ни одной кампании для этого магазина — сначала запустите синхронизацию.")
            return

        _print_counts(campaigns)
        if args.counts_only:
            return
        print()

        if args.state:
            matches = [c for c in campaigns if c.state == args.state]
            print(f"=== Кампании со state == {args.state!r} ({len(matches)}) ===")
            for c in matches:
                _print_campaign_compact(c)
            return

        print("=== Все ключи, встречающиеся хоть раз в raw_payload (по всем кампаниям) ===")
        all_keys: set[str] = set()
        for c in campaigns:
            if c.raw_payload:
                all_keys.update(json.loads(c.raw_payload).keys())
        print(f"  {sorted(all_keys)}")
        print()

        print("=== По одному компактному примеру на каждое значение state ===")
        seen_states: set[str | None] = set()
        for c in campaigns:
            if c.state in seen_states:
                continue
            seen_states.add(c.state)
            _print_campaign_compact(c)
    finally:
        db.close()


if __name__ == "__main__":
    main()
