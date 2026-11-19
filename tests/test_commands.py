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
