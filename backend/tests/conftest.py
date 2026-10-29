import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Seeded students keep the same ids across tests; limits must not leak from one test into the next."""
    ratelimit._clear_all_for_tests()
    yield
