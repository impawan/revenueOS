from datetime import datetime, timezone

import pytest

from revenueos.events import EventStore
from revenueos.library import load_templates
from revenueos.retrieval import Retriever

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)  # Thursday


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def store():
    return EventStore(real_now=lambda: NOW)


@pytest.fixture
def templates():
    return load_templates()


@pytest.fixture
def retriever(templates):
    return Retriever(templates)
