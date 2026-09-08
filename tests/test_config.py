import os
import uuid
from pathlib import Path

import pytest

from backend import config


def test_env_int_clamps_to_declared_range(monkeypatch):
    monkeypatch.setenv("STEAMKB_TEST_INT", "2")
    assert config.env_int("STEAMKB_TEST_INT", 10, minimum=5) == 5

    monkeypatch.setenv("STEAMKB_TEST_INT", "20")
    assert config.env_int("STEAMKB_TEST_INT", 10, maximum=15) == 15


def test_env_int_error_names_the_variable(monkeypatch):
    monkeypatch.setenv("STEAMKB_TEST_INT", "not-an-integer")
    with pytest.raises(ValueError, match="STEAMKB_TEST_INT"):
        config.env_int("STEAMKB_TEST_INT", 10)


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_bool_accepts_true_values(monkeypatch, value):
    monkeypatch.setenv("STEAMKB_TEST_BOOL", value)
    assert config.env_bool("STEAMKB_TEST_BOOL") is True


@pytest.mark.parametrize("value", ["0", "false", "NO", "off", ""])
def test_env_bool_accepts_false_values(monkeypatch, value):
    monkeypatch.setenv("STEAMKB_TEST_BOOL", value)
    assert config.env_bool("STEAMKB_TEST_BOOL", default=True) is False


def test_env_bool_error_names_the_variable(monkeypatch):
    monkeypatch.setenv("STEAMKB_TEST_BOOL", "sometimes")
    with pytest.raises(ValueError, match="STEAMKB_TEST_BOOL"):
        config.env_bool("STEAMKB_TEST_BOOL")


def test_load_dotenv_preserves_existing_environment(monkeypatch):
    temp_dir = Path(__file__).parent / ".tmp"
    temp_dir.mkdir(exist_ok=True)
    env_path = temp_dir / f"config-{uuid.uuid4().hex}.env"
    env_path.write_text(
        "STEAMKB_EXISTING=from-file\nSTEAMKB_NEW_VALUE=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STEAMKB_EXISTING", "from-process")
    monkeypatch.delenv("STEAMKB_NEW_VALUE", raising=False)

    try:
        config.load_dotenv(env_path)

        assert os.environ["STEAMKB_EXISTING"] == "from-process"
        assert os.environ["STEAMKB_NEW_VALUE"] == "from-file"
    finally:
        env_path.unlink(missing_ok=True)
