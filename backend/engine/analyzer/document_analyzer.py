from __future__ import annotations


def select_sample_pages(page_count: int, max_samples: int = 8) -> tuple[int, ...]:
    """Return deterministic, evenly distributed zero-based page indices."""
    if page_count <= 0:
        raise ValueError("page_count must be positive")
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")

    sample_count = min(page_count, max_samples)
    if sample_count == page_count:
        return tuple(range(page_count))
    if sample_count == 1:
        return (0,)

    last_page = page_count - 1
    denominator = sample_count - 1
    return tuple(round(index * last_page / denominator) for index in range(sample_count))
