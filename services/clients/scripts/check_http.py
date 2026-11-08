"""Synthetic checks against an isolated loopback Clients HTTP process.

This script uses only the standard library, so CI can run it against the image
without installing the application or its test dependencies on the runner.
"""

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


class Client:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Use an isolated service on loopback")
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("Expected a loopback HTTP URL without credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Expected an HTTP origin without a path, query or fragment")
        self.base_url = base_url.rstrip("/")

    def call(self, method, path, payload=None, expected=200):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = json.load(response)
            if response.status != expected:
                raise AssertionError(f"{method} {path}: expected {expected}, got {response.status}")
            return body


def check_closed(client):
    code, batch_id = "check-absent", str(uuid4())
    for method, path in [
        ("GET", "/clients"),
        ("GET", f"/clients/{code}"),
        ("GET", f"/clients/{code}/snapshots/history"),
        ("POST", "/import/apply"),
        ("POST", f"/import/{batch_id}/rollback"),
        ("DELETE", f"/clients/{code}"),
        ("DELETE", f"/clients/{code}/snapshots/2026_Q1"),
    ]:
        client.call(method, "/internal/v1" + path, expected=404)
    paths = client.call("GET", "/openapi.json")["paths"]
    assert not any(path.startswith("/internal/v1/") for path in paths)
    return ["all_business_routes_closed", "openapi_excludes_business_routes"]


def check_business(client):
    actor, code = str(uuid4()), f"http-check-{uuid4().hex}"
    peer = code + "-peer"

    def apply(kind, items, day):
        payload = {
            "batch_id": str(uuid4()),
            "uploaded_by": actor,
            "import_type": kind,
            "selected_quarter": "2026_Q2",
            "snapshot_date": day,
            "items": items,
        }
        result = client.call("POST", "/internal/v1/import/apply", payload)
        assert result["rejected_rows"] == 0
        return payload, result

    def snapshots(first, second):
        return [
            {
                "client": {"kcr_code": code},
                "snapshot": {
                    "quarter": quarter,
                    "total_documents": 100,
                    "unsubmitted_documents": count,
                },
            }
            for quarter, count in [("2026_Q1", first), ("2026_Q2", second)]
        ]

    def command():
        return {"operation_id": str(uuid4()), "requested_by": actor}

    def rollback(batch_id):
        result = client.call("POST", f"/internal/v1/import/{batch_id}/rollback", command())
        assert result["local_only"] and not result["already_rolled_back"]

    registry, _ = apply(
        "REGISTRY",
        [
            {"client": {"kcr_code": code, "tags": ["VIP"]}},
            {"client": {"kcr_code": peer}},
        ],
        "2026-04-01",
    )
    initial, _ = apply("SNAPSHOT", snapshots(10, 20), "2026-04-10")
    changed, applied = apply("SNAPSHOT", snapshots(7, 15), "2026-04-20")
    assert client.call("POST", "/internal/v1/import/apply", changed) == applied
    path = "/internal/v1/clients/" + code
    card = client.call("GET", path + "?quarter=2026_Q2&snapshots_limit=1")
    assert card["outstanding_documents"] == 22 and card["dynamics"] == "GROWING"
    assert len(card["snapshots"]) == 1
    assert card["quarter_summary"]["unsubmitted_percent"] == 15
    selected = client.call(
        "GET",
        "/internal/v1/clients?quarter=2026_Q2&search="
        + code
        + "&min_unsubmitted_documents=15&max_unsubmitted_documents=15",
    )
    assert selected["total"] == 1 and selected["items"][0]["kcr_code"] == code
    assert client.call("GET", path + "/snapshots/history")["total"] == 4
    deletion = command()
    removed = client.call("DELETE", path + "/snapshots/2026_Q1", deletion)
    assert removed["local_only"]
    assert client.call("GET", path)["outstanding_documents"] == 15
    rollback(changed["batch_id"])
    assert client.call("GET", path)["outstanding_documents"] == 30
    assert client.call("GET", path + "/snapshots/history")["total"] == 2
    assert client.call("DELETE", path + "/snapshots/2026_Q1", deletion) == removed
    assert client.call("GET", path)["outstanding_documents"] == 30
    assert client.call("GET", "/internal/v1/clients/" + peer)["kcr_code"] == peer
    rollback(initial["batch_id"])
    assert client.call("GET", path)["outstanding_documents"] == 0
    rollback(registry["batch_id"])
    client.call("GET", path, expected=404)
    client.call("GET", "/internal/v1/clients/" + peer, expected=404)
    return [
        "apply_and_replay",
        "registry_and_numeric_filter",
        "history",
        "delete_quarter",
        "restore_after_manual_deletion",
        "replay_does_not_delete_restored_data",
        "other_client_preserved",
        "synthetic_clients_cleaned_up",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=["closed", "business"], default="closed")
    args = parser.parse_args()
    client = Client(args.base_url)
    for path in ("/health/live", "/health/ready"):
        assert client.call("GET", path) == {"status": "ok"}
    checks = check_closed(client) if args.mode == "closed" else check_business(client)
    print(json.dumps({"mode": args.mode, "status": "passed", "checks": checks}))


if __name__ == "__main__":
    main()
