import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "api"))
os.environ.setdefault("TABLE_NAME", "test-table")

import app  # noqa: E402


class FakeTable:
    """Just enough of a DynamoDB Table for the handler."""

    def __init__(self):
        self.items = {}

    def update_item(self, Key, **_):
        item = self.items.setdefault(Key["pk"], {"pk": Key["pk"], "n": 0})
        item["n"] += 1
        return {"Attributes": {"n": item["n"]}}

    def get_item(self, Key):
        item = self.items.get(Key["pk"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item["pk"]] = Item


@pytest.fixture(autouse=True)
def fake_table(monkeypatch):
    t = FakeTable()
    monkeypatch.setattr(app, "_table", t)
    return t


def call(method, path, body=None):
    event = {"httpMethod": method, "path": path}
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    res = app.handler(event, None)
    return res["statusCode"], json.loads(res["body"])


def test_visit_counter_increments():
    assert call("POST", "/api/visit") == (200, {"count": 1})
    assert call("POST", "/api/visit") == (200, {"count": 2})


def test_get_visits_reads_without_incrementing():
    call("POST", "/api/visit")
    assert call("GET", "/api/visits") == (200, {"count": 1})
    assert call("GET", "/api/visits") == (200, {"count": 1})


def test_visits_starts_at_zero():
    assert call("GET", "/api/visits") == (200, {"count": 0})


def test_contact_stores_message_and_counts_it(fake_table):
    status, body = call("POST", "/api/contact", {"name": "Ada", "email": "ada@example.com", "message": "Hi there"})
    assert (status, body) == (201, {"ok": True})
    stored = [i for k, i in fake_table.items.items() if k.startswith("MSG#")]
    assert len(stored) == 1 and stored[0]["name"] == "Ada"
    assert fake_table.items["MSGCOUNT"]["n"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "email": "a@b.co", "message": "hi"},
        {"name": "Ada", "email": "not-an-email", "message": "hi"},
        {"name": "Ada", "email": "a@b.co", "message": ""},
        {"name": "Ada", "email": "a@b.co", "message": "x" * 2001},
    ],
)
def test_contact_rejects_bad_input(payload, fake_table):
    status, _ = call("POST", "/api/contact", payload)
    assert status == 400
    assert not any(k.startswith("MSG#") for k in fake_table.items)


def test_contact_rejects_non_json():
    assert call("POST", "/api/contact", "{nope")[0] == 400


def test_contact_honeypot_pretends_success_but_stores_nothing(fake_table):
    status, _ = call("POST", "/api/contact", {"name": "Bot", "email": "b@b.co", "message": "spam", "website": "http://x"})
    assert status == 201
    assert not any(k.startswith("MSG#") for k in fake_table.items)


def test_unknown_route_is_404():
    assert call("GET", "/api/nope")[0] == 404


@pytest.mark.parametrize(
    "event",
    [
        {"source": "aws.events", "detail-type": "Scheduled Event"},  # what EventBridge really sends
        {"source": "aws.events"},
        {"detail-type": "Scheduled Event"},
    ],
)
def test_eventbridge_event_writes_daily_snapshot(fake_table, event):
    call("POST", "/api/visit")
    call("POST", "/api/visit")
    result = app.handler(event, None)
    assert result["ok"] is True and result["visits"] == 2 and result["messages"] == 0
    assert any(k.startswith("STATS#") for k in fake_table.items)
