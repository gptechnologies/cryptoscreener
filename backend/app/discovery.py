from __future__ import annotations

"""Conservative extraction of Solana pairs from New Pairs socket frames.

Dexscreener's browser socket is undocumented. Unknown envelopes are ignored;
never guess a pair address from an arbitrary string in a frame.
"""

import json
import re


ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def pairs_from_frame(payload: str | bytes) -> list[dict]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if len(payload) > 10_000_000:
        return []
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return []
    found = {}

    def visit(value, depth=0, chain=None):
        if depth > 8 or len(found) >= 500:
            return
        if isinstance(value, list):
            for child in value:
                visit(child, depth + 1, chain)
        elif isinstance(value, dict):
            current_chain = value.get("chainId") or value.get("chain") or chain
            address = value.get("pairAddress")
            if current_chain == "solana" and isinstance(address, str) and ADDRESS.fullmatch(address):
                base = value.get("baseToken") or {}
                symbol = base.get("symbol") if isinstance(base, dict) else None
                symbol = symbol or value.get("baseTokenSymbol") or value.get("symbol") or "…"
                found[address.lower()] = {"address": address, "symbol": str(symbol)[:40]}
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child, depth + 1, current_chain)

    visit(data)
    return list(found.values())
