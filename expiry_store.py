from datetime import datetime, date as ddate
from typing import List
import json
import os

from config import SCRIPMASTERFILE, EXPIRY_STORE_FILE


def load_expiry_store() -> List[str]:
    if not os.path.exists(EXPIRY_STORE_FILE):
        return []
    try:
        with open(EXPIRY_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
        return []
    except Exception as e:
        print("load_expiry_store error", e)
        return []


def save_expiry_store(expiries: List[str]) -> None:
    try:
        uniq = sorted(set(expiries))
        with open(EXPIRY_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(uniq, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_expiry_store error", e)


def fetch_expiries_from_scripmaster() -> List[str]:
    try:
        with open(SCRIPMASTERFILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        records = data if isinstance(data, list) else data.get("data", [])
        expiries: set[str] = set()

        for row in records:
            if (
                row.get("exch_seg") == "NFO"
                and row.get("name") == "NIFTY"
                and row.get("instrumenttype") == "OPTIDX"
            ):
                raw_exp = row.get("expiry", "")
                if not raw_exp:
                    continue

                dt = None
                try:
                    dt = datetime.strptime(raw_exp.upper(), "%d%b%Y")
                except Exception:
                    try:
                        dt = datetime.strptime(raw_exp, "%Y-%m-%d")
                    except Exception:
                        dt = None

                if dt is not None:
                    expiries.add(dt.strftime("%Y-%m-%d"))

        return sorted(expiries)
    except Exception as e:
        print("fetch_expiries_from_scripmaster error", e)
        return []


def refresh_and_get_expiries(include_past: bool = False) -> List[str]:
    local = load_expiry_store()
    from_scrip = fetch_expiries_from_scripmaster()

    merged = sorted(set(local) | set(from_scrip))
    save_expiry_store(merged)

    if not include_past:
        today = ddate.today()
        merged = [
            e for e in merged
            if datetime.strptime(e, "%Y-%m-%d").date() >= today
        ]
    return merged
