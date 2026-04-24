from nova_navigator.base.thread_safe_list import ThreadSafeList


def test_thread_safe_list() -> None:
    tsl = ThreadSafeList[int]([1, 2, 3])

    assert len(tsl) == 3

    tsl.append(4)

    assert len(tsl) == 4
    assert tsl[0] == 1
    assert tsl[1] == 2
    assert tsl[2] == 3
    assert tsl[3] == 4

    expected = [1, 2, 3, 4]
    with tsl as lst:
        for v in zip(lst, expected, strict=True):
            assert v[0] == v[1]

    item = tsl.pop()
    assert item == 4
    assert len(tsl) == 3

    item = tsl.pop_front()
    assert item == 1
    assert len(tsl) == 2
