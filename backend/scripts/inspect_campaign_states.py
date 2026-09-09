"""One-off diagnostic script: does NOT call Ozon — reads what the app itself
already stored in advertising_campaigns.raw_payload (the full, unfiltered JSON
Ozon sent for GET /api/client/campaign, captured via OzonCampaignItem's
extra="allow") for campaigns already synced into this store.

Written to debug the reported symptom: the "Активные" tab shows campaigns
with state == "CAMPAIGN_STATE_INACTIVE" (e.g. "апт/2+1/табл/тон ПОЛКИ")
mixed in with "CAMPAIGN_STATE_RUNNING" ones, when the user expects paused-
but-not-archived campaigns to NOT count as active, or at least wants to know
whether Ozon exposes a distinct archival flag/field beyond `state` that our
current filter (state != "CAMPAIGN_STATE_ARCHIVED") isn't using.

AdvertisingCampaign.state is stored as free text (see the model's own
docstring: "the full set of possible values was not exhaustively confirmed
against current docs") and raw_payload is the campaign's *entire* JSON as
Ozon returned it (extra fields included, since OzonCampaignItem uses
extra="allow") — so this script answers two things without ever guessing:
  (a) the full set of distinct `state` values actually present for this
      store's campaigns, with a count for each;
  (b) one complete raw_payload example per distinct state — including any
      field beyond `state` (e.g. a boolean archive flag) our current model
      doesn't parse but Ozon may still be sending.

Usage (inside the running container):

    docker compose exec app python backend/scripts/inspect_campaign_states.py --store-id <id>

    # also print the full raw_payload for one specific campaign by name
    # (substring, case-insensitive):
    docker compose exec app python backend/scripts/inspect_campaign_states.py \\
        --store-id <id> --campaign-name "апт/2+1/табл/тон ПОЛКИ"
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


def _print_campaign(c: AdvertisingCampaign) -> None:
    print(f"  id={c.ozon_campaign_id} name={c.name!r} state={c.state!r} type={c.campaign_type!r}")
    if c.raw_payload:
        print("  raw_payload:")
        print("  " + json.dumps(json.loads(c.raw_payload), ensure_ascii=False, indent=2).replace("\n", "\n  "))
    else:
        print("  (raw_payload пуст — кампания синхронизирована до того, как оно стало сохраняться)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--campaign-name", default=None, help="Substring match, case-insensitive")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        campaigns = db.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == args.store_id).all()
        if not campaigns:
            print("Нет ни одной кампании для этого магазина — сначала запустите синхронизацию.")
            return

        print(f"Всего кампаний в базе: {len(campaigns)}")
        print()
        print("=== Распределение по state ===")
        counts = Counter(c.state for c in campaigns)
        for state, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {state!r}: {n}")
        print()

        print("=== По одному полному примеру raw_payload на каждое значение state ===")
        seen_states: set[str | None] = set()
        for c in campaigns:
            if c.state in seen_states:
                continue
            seen_states.add(c.state)
            print("-" * 70)
            _print_campaign(c)
        print()

        if args.campaign_name:
            matches = [c for c in campaigns if c.name and args.campaign_name.lower() in c.name.lower()]
            print(f"=== Кампании с именем, содержащим {args.campaign_name!r} ===")
            if not matches:
                print("  Не найдено.")
            for c in matches:
                print("-" * 70)
                _print_campaign(c)
    finally:
        db.close()


if __name__ == "__main__":
    main()
