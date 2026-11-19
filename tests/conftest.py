import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def nf():
    from name_filter import NameFilter
    return NameFilter()          # словари грузятся один раз на все тесты
