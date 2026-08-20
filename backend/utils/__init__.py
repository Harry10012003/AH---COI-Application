from __future__ import annotations

import re


def normalize_text(text: object) -> str:
    """Normalize text for color/description matching.

    Uppercases, strips ``@`` and non-breaking spaces, removes all
    non-alphanumeric characters and collapses whitespace.
    """
    raw = str(text or "").upper().replace("@", " ").replace("\xa0", " ")
    raw = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def normalize_ppo(text: object) -> str:
    """Return a canonical uppercase alphanumeric-only PPO key."""
    raw = str(text or "").upper().replace("\xa0", " ").strip()
    raw = re.sub(r"\s+", "", raw)
    return re.sub(r"[^A-Z0-9]", "", raw)


def safe_float(value: object) -> float:
    """Parse *value* into a float, returning ``0.0`` on failure."""
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def infer_brand(go_summary: dict, ppo_infos: dict[str, dict]) -> str:
    """Best-effort brand/buyer inference from GO summary and PPO info dicts."""
    buyer = str(go_summary.get("buyer") or "").strip()
    if buyer:
        return buyer
    for info in ppo_infos.values():
        brand = str(info.get("brand") or "").strip()
        if brand:
            return brand
    style_no = str(go_summary.get("style_no") or "").upper()
    if style_no.startswith("GIO-"):
        return "GIORDANO"
    return ""
