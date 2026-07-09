from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence


def bounded_int(seed: str, minimum: int, maximum: int, *parts: object) -> int:
    span = maximum - minimum + 1
    if span <= 0:
        raise ValueError("maximum must be greater than or equal to minimum")
    return minimum + (digest_int(seed, *parts) % span)


def deterministic_shuffle(
    seed: str,
    deck: str,
    shuffle_counter: int,
    card_ids: Sequence[str],
) -> tuple[str, ...]:
    shuffled = list(card_ids)
    for index in range(len(shuffled) - 1, 0, -1):
        swap_index = digest_int(seed, "deck_shuffle", deck, shuffle_counter, index) % (index + 1)
        shuffled[index], shuffled[swap_index] = shuffled[swap_index], shuffled[index]
    return tuple(shuffled)


def digest_int(seed: str, *parts: object) -> int:
    payload = json.dumps([seed, *parts], ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest, byteorder="big")
