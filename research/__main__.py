"""Read a single-currency signal basket and print a reproducible JSON simulation."""
import argparse
from dataclasses import asdict
import hashlib
import json
import subprocess
from pathlib import Path

from .allocation import AllocationPolicy, allocate
from .signals import SelectionSignal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("input", nargs="?", type=Path, help="JSON-mandje")
    source.add_argument("--supabase", action="store_true", help="Recent USD-mandje uit Supabase")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--config", type=Path, default=Path("config/research.json"))
    parser.add_argument("--telegram", action="store_true", help="Stuur simulatie naar Telegram")
    args = parser.parse_args()
    if args.supabase:
        from .database import load_basket
        data = load_basket(max_age_days=args.max_age_days)
    else:
        data = json.loads(args.input.read_text())
    if not isinstance(data.get("currency"), str) or not data["currency"].strip():
        raise ValueError("Geef de gemeenschappelijke valuta op; converteer koersen eerst")
    if not data.get("data_provenance"):
        raise ValueError("data_provenance is verplicht")
    policy = AllocationPolicy(**json.loads(args.config.read_text()))
    signals = [SelectionSignal(**s) for s in data["signals"]]
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    # Include actual implementation bytes: uncommitted edits cannot share a fingerprint.
    code = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))}
    manifest = {"schema_version": 1, "mode": "cash_only_basket_simulation",
                "currency": data["currency"], "data_provenance": data["data_provenance"],
                "signals": [s.payload() for s in sorted(signals, key=lambda s: s.ticker)],
                "policy": asdict(policy), "revision": revision, "code": code}
    canonical = json.dumps(manifest, sort_keys=True, allow_nan=False, separators=(",", ":"))
    run_id = hashlib.sha256(canonical.encode()).hexdigest()
    result = {"run_id": run_id, "manifest": manifest,
              "simulation": allocate(signals, policy)}
    print(json.dumps(result, indent=2, allow_nan=False))
    if args.telegram:
        from .telegram import format_result, send_text
        send_text(format_result(result))


if __name__ == "__main__":
    main()
