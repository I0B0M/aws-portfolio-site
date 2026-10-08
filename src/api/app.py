"""Portfolio backend: visitor counter, contact form, and a daily stats snapshot.

One DynamoDB table holds everything, keyed by `pk`:
  COUNTER          running total of page visits
  MSGCOUNT         running total of contact messages
  MSG#<ts>#<id>    one contact message
  STATS#<date>     one daily snapshot, written by the EventBridge schedule
  LATEST           copy of the newest snapshot, so the site can read it in one call
  QUOTES           latest stock quotes from Alpaca, refreshed by an EventBridge schedule
"""
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

_table = None
_creds = None

SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "AMZN,NVDA,AAPL").split(",") if s.strip()]
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/snapshots"

MAX_NAME = 80
MAX_EMAIL = 120
MAX_MESSAGE = 2000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


class MarketDataError(Exception):
    """Something went wrong getting quotes; the message is safe to show to visitors."""


def credentials():
    """Alpaca key id + secret, read once from encrypted Parameter Store values."""
    global _creds
    if _creds is None:
        names = [os.environ["ALPACA_KEY_PARAM"], os.environ["ALPACA_SECRET_PARAM"]]
        try:
            res = boto3.client("ssm").get_parameters(Names=names, WithDecryption=True)
        except ClientError:
            raise MarketDataError("Market data keys could not be read.") from None
        found = {p["Name"]: p["Value"] for p in res["Parameters"]}
        if len(found) != len(names):
            raise MarketDataError("Market data is not configured yet.")
        _creds = (found[names[0]], found[names[1]])
    return _creds


def fetch_snapshots(symbols):
    key, secret = credentials()
    req = urllib.request.Request(
        f"{ALPACA_URL}?symbols={','.join(symbols)}&feed=iex",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        raise MarketDataError(f"Market data provider returned {err.code}.") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise MarketDataError("Could not reach the market data provider.") from None
    return data.get("snapshots", data)


def build_quotes(snapshots):
    """Turn Alpaca snapshots into the small shape the page needs."""
    quotes = []
    for symbol in SYMBOLS:
        snap = snapshots.get(symbol)
        if not snap:
            continue
        trade = snap.get("latestTrade") or {}
        today = snap.get("dailyBar") or {}
        price = trade.get("p")
        if price is None:
            price = today.get("c")
        if price is None:
            continue
        prev = (snap.get("prevDailyBar") or {}).get("c")
        change = round(price - prev, 2) if prev else None
        quotes.append(
            {
                "symbol": symbol,
                "price": round(price, 2),
                "prevClose": round(prev, 2) if prev else None,
                "change": change,
                "changePercent": round(change / prev * 100, 2) if prev else None,
                "open": round(today["o"], 2) if today.get("o") is not None else None,
                "tradeTime": trade.get("t"),
            }
        )
    return quotes


def refresh_quotes():
    quotes = build_quotes(fetch_snapshots(SYMBOLS))
    if not quotes:
        raise MarketDataError("The provider returned no quotes.")
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    table().put_item(Item={"pk": "QUOTES", "quotes": json.dumps(quotes), "updatedAt": updated})
    return quotes, updated


def scheduled_refresh():
    """EventBridge entry point: fetch fresh quotes and store them."""
    try:
        quotes, updated = refresh_quotes()
    except MarketDataError as err:
        print(json.dumps({"event": "quotes_refresh_failed", "error": str(err)}))
        return {"ok": False, "error": str(err)}
    print(json.dumps({"event": "quotes_refreshed", "count": len(quotes)}))
    return {"ok": True, "count": len(quotes), "updatedAt": updated}


def get_quotes():
    """Serve stored quotes; on a cold start with nothing stored, fetch once."""
    item = table().get_item(Key={"pk": "QUOTES"}).get("Item")
    if item:
        quotes, updated = json.loads(item["quotes"]), item["updatedAt"]
    else:
        try:
            quotes, updated = refresh_quotes()
        except MarketDataError as err:
            return respond(503, {"error": str(err)})
    return respond(
        200,
        {
            "quotes": quotes,
            "updatedAt": updated,
            "source": "Alpaca Market Data (IEX feed)",
            "servedBy": "AWS Lambda",
            "region": os.environ.get("AWS_REGION", "unknown"),
        },
    )


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def bump(pk):
    """Atomically add 1 to a counter item and return the new value."""
    result = table().update_item(
        Key={"pk": pk},
        UpdateExpression="ADD #n :one",
        ExpressionAttributeNames={"#n": "n"},
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(result["Attributes"]["n"])


def read_count(pk):
    item = table().get_item(Key={"pk": pk}).get("Item")
    return int(item["n"]) if item else 0


def record_visit():
    return respond(200, {"count": bump("COUNTER")})


def get_visits():
    return respond(200, {"count": read_count("COUNTER")})


def get_stats():
    """Everything the page's 'Live from AWS' section shows, in one response."""
    latest = table().get_item(Key={"pk": "LATEST"}).get("Item")
    snapshot = None
    if latest:
        snapshot = {
            "date": latest["date"],
            "visits": int(latest["visits"]),
            "messages": int(latest["messages"]),
        }
    return respond(
        200,
        {
            "visits": read_count("COUNTER"),
            "messages": read_count("MSGCOUNT"),
            "latestSnapshot": snapshot,
            "servedBy": "AWS Lambda",
            "region": os.environ.get("AWS_REGION", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def submit_contact(raw_body):
    try:
        data = json.loads(raw_body or "{}")
    except ValueError:
        return respond(400, {"error": "Body must be JSON."})
    if not isinstance(data, dict):
        return respond(400, {"error": "Body must be a JSON object."})

    # Hidden field real visitors never fill in; bots do. Pretend it worked.
    if data.get("website"):
        return respond(201, {"ok": True})

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip()
    message = str(data.get("message", "")).strip()

    if not name or len(name) > MAX_NAME:
        return respond(400, {"error": "Please enter your name."})
    if not EMAIL_RE.match(email) or len(email) > MAX_EMAIL:
        return respond(400, {"error": "Please enter a valid email."})
    if not message or len(message) > MAX_MESSAGE:
        return respond(400, {"error": "Please enter a message (2000 characters max)."})

    now = datetime.now(timezone.utc).isoformat()
    table().put_item(
        Item={
            "pk": f"MSG#{now}#{uuid.uuid4().hex[:8]}",
            "name": name,
            "email": email,
            "message": message,
            "createdAt": now,
        }
    )
    bump("MSGCOUNT")
    return respond(201, {"ok": True})


def daily_snapshot():
    """EventBridge entry point: freeze today's totals into a STATS#<date> item."""
    today = datetime.now(timezone.utc).date().isoformat()
    item = {
        "pk": f"STATS#{today}",
        "visits": read_count("COUNTER"),
        "messages": read_count("MSGCOUNT"),
    }
    table().put_item(Item=item)
    table().put_item(Item={**item, "pk": "LATEST", "date": today})
    print(json.dumps({"event": "daily_snapshot", **item}))
    return {"ok": True, **item}


def handler(event, context):
    # Scheduled invocations come from EventBridge, not API Gateway.
    if event.get("task") == "refresh_quotes":
        return scheduled_refresh()
    if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
        return daily_snapshot()

    method = event.get("httpMethod")
    path = event.get("path", "")

    if method == "POST" and path.endswith("/api/visit"):
        return record_visit()
    if method == "GET" and path.endswith("/api/visits"):
        return get_visits()
    if method == "GET" and path.endswith("/api/quotes"):
        return get_quotes()
    if method == "GET" and path.endswith("/api/stats"):
        return get_stats()
    if method == "POST" and path.endswith("/api/contact"):
        return submit_contact(event.get("body"))
    return respond(404, {"error": "Not found."})
