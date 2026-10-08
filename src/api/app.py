"""Portfolio backend: visitor counter, contact form, and a daily stats snapshot.

One DynamoDB table holds everything, keyed by `pk`:
  COUNTER          running total of page visits
  MSGCOUNT         running total of contact messages
  MSG#<ts>#<id>    one contact message
  STATS#<date>     one daily snapshot, written by the EventBridge schedule
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3

_table = None

MAX_NAME = 80
MAX_EMAIL = 120
MAX_MESSAGE = 2000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


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
    print(json.dumps({"event": "daily_snapshot", **item}))
    return {"ok": True, **item}


def handler(event, context):
    # Scheduled invocations come from EventBridge, not API Gateway.
    if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
        return daily_snapshot()

    method = event.get("httpMethod")
    path = event.get("path", "")

    if method == "POST" and path.endswith("/api/visit"):
        return record_visit()
    if method == "GET" and path.endswith("/api/visits"):
        return get_visits()
    if method == "POST" and path.endswith("/api/contact"):
        return submit_contact(event.get("body"))
    return respond(404, {"error": "Not found."})
