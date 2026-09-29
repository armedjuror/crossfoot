from crossfoot.config import get_settings


def test_default_settings():
    settings = get_settings()
    assert settings.mode == "playground"
    assert "crossfoot" in settings.database_url
