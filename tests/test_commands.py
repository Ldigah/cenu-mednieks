from commands import parse_command, slugify


def test_parse_command_basic():
    assert parse_command("/list") == ("list", "")
    assert parse_command("/add https://x.ee My thing") == ("add", "https://x.ee My thing")
    assert parse_command("plain text") is None
    assert parse_command("") is None
    assert parse_command("/") is None


def test_parse_command_bot_suffix():
    assert parse_command("/prices@CenuMednieksBot") == ("prices", "")


def test_slugify():
    assert slugify("UGREEN LP923 — laptop stand!") == "ugreen-lp923-laptop-stand"


def test_parse_command_hyphen_underscore_alias():
    # BotFather command names allow only a-z, 0-9, _ — /add-url and /add_url
    # must both reach the add_url handler.
    assert parse_command("/add-url https://x.ee/part jbl") == ("add_url", "https://x.ee/part jbl")
    assert parse_command("/add_url https://x.ee/part jbl") == ("add_url", "https://x.ee/part jbl")
    assert parse_command("/ADD-URL https://x.ee") == ("add_url", "https://x.ee")
