from nova_navigator.scheduler import SkippedItem, skipped_summary


def _items(*reasons: str) -> list[SkippedItem]:
    return [SkippedItem(f"item{index}", reason) for index, reason in enumerate(reasons)]


def test_three_skipped_links_mentions_count_and_link() -> None:
    text = skipped_summary(_items("broken link", "link loop", "broken link"))
    assert text is not None
    assert "3" in text
    assert "link" in text


def test_empty_returns_none() -> None:
    assert skipped_summary([]) is None


def test_singular_item() -> None:
    assert skipped_summary(_items("broken link")) == "Skipped 1 item: broken link (1)"


def test_groups_by_reason_in_first_seen_order() -> None:
    text = skipped_summary(_items("broken link", "link loop", "broken link"))
    assert text == "Skipped 3 items: broken link (2), link loop (1)"


def test_empty_reasons_omitted() -> None:
    assert skipped_summary(_items("", "")) == "Skipped 2 items"
    assert skipped_summary(_items("", "link loop")) == "Skipped 2 items: link loop (1)"


def test_more_than_three_reasons_ellipsis() -> None:
    text = skipped_summary(_items("a", "b", "c", "d"))
    assert text == "Skipped 4 items: a (1), b (1), c (1), …"
