import pytest

from protocol_archive.db import Repository, connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.sqlite3")
    yield c
    c.close()


@pytest.fixture
def repo(conn):
    return Repository(conn)


@pytest.fixture
def make_file(tmp_path):
    """Создаёт тестовый файл-«оригинал» в отдельной папке."""
    src = tmp_path / "originals"
    src.mkdir()

    def _make(name: str, content: bytes = b"test-content"):
        p = src / name
        p.write_bytes(content)
        return p

    return _make
