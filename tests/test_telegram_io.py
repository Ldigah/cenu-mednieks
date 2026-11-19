import telegram_io


def test_payload_updates():
    event = {"client_payload": {"commands": [
        {"text": "/add anker soundcore 2", "chat_id": 497422706},
        {"text": "", "chat_id": 497422706},
        {"chat_id": 497422706},
    ]}}
    ups = telegram_io.payload_updates(event)
    assert len(ups) == 1
    assert ups[0]["message"]["text"] == "/add anker soundcore 2"
    assert ups[0]["message"]["chat"]["id"] == 497422706
    assert telegram_io.payload_updates({}) == []
