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


def test_stats_before_any_snapshot(fake_table):
    call("POST", "/api/visit")
    status, body = call("GET", "/api/stats")
    assert status == 200
    assert body["visits"] == 1 and body["messages"] == 0
    assert body["latestSnapshot"] is None
    assert body["servedBy"] == "AWS Lambda" and body["timestamp"]


def test_stats_reports_latest_snapshot(fake_table):
    call("POST", "/api/visit")
    call("POST", "/api/visit")
    app.handler({"source": "aws.events"}, None)
    call("POST", "/api/visit")
    status, body = call("GET", "/api/stats")
    assert status == 200
    assert body["visits"] == 3
    assert body["latestSnapshot"]["visits"] == 2  # frozen at snapshot time, not live
    assert body["latestSnapshot"]["date"]


# ---- stock quotes (Alpaca) ----

def snapshot(price, prev_close, open_=None):
    return {
        "latestTrade": {"t": "2026-10-07T19:59:58Z", "p": price},
        "dailyBar": {"o": open_ if open_ is not None else price, "c": price},
        "prevDailyBar": {"c": prev_close},
    }


@pytest.fixture
def alpaca(monkeypatch):
    calls = []

    def fake_fetch(symbols):
        calls.append(list(symbols))
        return {
            "AMZN": snapshot(110.0, 100.0, 101.5),
            "NVDA": snapshot(95.5, 100.0),
            "AAPL": snapshot(50.0, 50.0),
        }

    monkeypatch.setattr(app, "fetch_snapshots", fake_fetch)
    return calls


def test_build_quotes_computes_change_and_percent():
    quotes = {q["symbol"]: q for q in app.build_quotes({"AMZN": snapshot(110.0, 100.0, 101.5), "NVDA": snapshot(95.5, 100.0)})}
    assert quotes["AMZN"]["change"] == 10.0 and quotes["AMZN"]["changePercent"] == 10.0
    assert quotes["AMZN"]["open"] == 101.5 and quotes["AMZN"]["prevClose"] == 100.0
    assert quotes["NVDA"]["change"] == -4.5 and quotes["NVDA"]["changePercent"] == -4.5


def test_build_quotes_without_previous_close_has_no_change():
    q = app.build_quotes({"AMZN": {"latestTrade": {"p": 10.0}}})[0]
    assert q["price"] == 10.0 and q["change"] is None and q["changePercent"] is None


def test_build_quotes_falls_back_to_daily_close_and_skips_empty():
    quotes = app.build_quotes({"AMZN": {"dailyBar": {"c": 12.0}}, "NVDA": {}})
    assert [q["symbol"] for q in quotes] == ["AMZN"] and quotes[0]["price"] == 12.0


def test_quotes_cold_start_fetches_once_then_serves_stored(alpaca, fake_table):
    status, body = call("GET", "/api/quotes")
    assert status == 200 and len(body["quotes"]) == 3 and body["servedBy"] == "AWS Lambda"
    assert body["source"].startswith("Alpaca") and body["updatedAt"]
    status, body = call("GET", "/api/quotes")
    assert status == 200 and len(alpaca) == 1  # second call came from DynamoDB, not Alpaca


def test_quotes_returns_503_with_friendly_message_when_unconfigured(monkeypatch):
    def boom(symbols):
        raise app.MarketDataError("Market data is not configured yet.")

    monkeypatch.setattr(app, "fetch_snapshots", boom)
    status, body = call("GET", "/api/quotes")
    assert status == 503 and body == {"error": "Market data is not configured yet."}


def test_scheduled_refresh_stores_quotes_for_the_page(alpaca, fake_table):
    result = app.handler({"task": "refresh_quotes"}, None)
    assert result["ok"] is True and result["count"] == 3
    assert "QUOTES" in fake_table.items
    status, body = call("GET", "/api/quotes")
    assert status == 200 and len(alpaca) == 1


def test_scheduled_refresh_failure_does_not_raise(monkeypatch, fake_table):
    def boom(symbols):
        raise app.MarketDataError("Could not reach the market data provider.")

    monkeypatch.setattr(app, "fetch_snapshots", boom)
    result = app.handler({"task": "refresh_quotes"}, None)
    assert result == {"ok": False, "error": "Could not reach the market data provider."}
    assert "QUOTES" not in fake_table.items


def test_fetch_sends_keys_in_headers_not_url(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"AMZN": {}}'

    def fake_urlopen(req, timeout):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return Resp()

    monkeypatch.setattr(app, "credentials", lambda: ("KEYID", "SECRET"))
    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    app.fetch_snapshots(["AMZN", "NVDA"])
    assert "symbols=AMZN,NVDA" in seen["url"] and "feed=iex" in seen["url"]
    assert "KEYID" not in seen["url"] and "SECRET" not in seen["url"]
    assert seen["headers"]["Apca-api-key-id"] == "KEYID" and seen["headers"]["Apca-api-secret-key"] == "SECRET"


def test_provider_http_error_becomes_safe_message(monkeypatch):
    def fake_urlopen(req, timeout):
        raise app.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(app, "credentials", lambda: ("KEYID", "SECRET"))
    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(app.MarketDataError) as err:
        app.fetch_snapshots(["AMZN"])
    assert str(err.value) == "Market data provider returned 403."
    assert "SECRET" not in str(err.value)
