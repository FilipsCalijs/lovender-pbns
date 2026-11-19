from verify_filters import num


def test_numbers():
    assert num("1.1 K") == 1100
    assert num("7.4 K") == 7400
    assert num("1.2 M") == 1_200_000
    assert num("65535") == 65535
    assert num("0.38") == 0.38
    assert num("-") is None
    assert num("") is None
    assert num(None) is None
