from __future__ import annotations

from collections import Counter
from hashlib import sha256


def page_stream_hashes(doc, page) -> tuple[str, ...]:
    hashes = []
    for xref in page.get_contents():
        try:
            payload = doc.xref_stream(xref)
        except Exception:
            continue
        hashes.append(sha256(payload).hexdigest())
    return tuple(hashes)


def repeated_hashes(page_hashes: tuple[tuple[str, ...], ...]) -> frozenset[str]:
    counts = Counter(hash_value for hashes in page_hashes for hash_value in set(hashes))
    return frozenset(hash_value for hash_value, count in counts.items() if count >= 2)
