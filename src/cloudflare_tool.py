"""CLI for managing Cloudflare DNS zones and records."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    """Raised for API or input errors."""


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("Expected one of: true/false, yes/no, on/off, 1/0")


def _is_uuid_like(value: str) -> bool:
    clean = value.replace("-", "")
    return len(clean) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in clean)


class CloudflareClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        if query:
            encoded = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None},
                doseq=True,
            )
            if encoded:
                url = f"{url}?{encoded}"

        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            raise CloudflareError(f"Cloudflare API HTTP {exc.code}: {raw_error}") from exc
        except urllib.error.URLError as exc:
            raise CloudflareError(f"Network error: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CloudflareError("Cloudflare API returned invalid JSON") from exc

        if not payload.get("success", False):
            errors = payload.get("errors") or []
            raise CloudflareError(f"Cloudflare API error: {json.dumps(errors, ensure_ascii=False)}")
        return payload

    def resolve_zone_id(self, zone: str) -> str:
        if _is_uuid_like(zone):
            return zone
        data = self.request("GET", "/zones", query={"name": zone, "per_page": 2})
        zones = data.get("result") or []
        if not zones:
            raise CloudflareError(f"Zone not found by name: {zone}")
        if len(zones) > 1:
            raise CloudflareError(f"Multiple zones matched name '{zone}'. Provide an explicit zone id.")
        return str(zones[0]["id"])

    def find_record(
        self,
        *,
        zone_id: str,
        record_id: str | None,
        name: str | None,
        record_type: str | None,
    ) -> dict[str, Any]:
        if record_id:
            data = self.request("GET", f"/zones/{zone_id}/dns_records/{record_id}")
            return data["result"]

        if not name or not record_type:
            raise CloudflareError("Provide --record-id or both --name and --type to identify the record.")

        data = self.request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            query={"name": name, "type": record_type, "per_page": 2},
        )
        matches = data.get("result") or []
        if not matches:
            raise CloudflareError(f"Record not found for name='{name}', type='{record_type}'")
        if len(matches) > 1:
            raise CloudflareError(
                f"Multiple records matched name='{name}', type='{record_type}'. Use --record-id."
            )
        return matches[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cloudflare_tool")
    parser.add_argument(
        "--token-env",
        default="CLOUDFLARE_API_TOKEN",
        help="Environment variable that stores Cloudflare API token.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    zones = sub.add_parser("zones", help="Zone operations.")
    zones_sub = zones.add_subparsers(dest="zones_command", required=True)
    zones_list = zones_sub.add_parser("list", help="List available zones.")
    zones_list.add_argument("--name", default=None, help="Optional exact zone name filter.")
    zones_list.add_argument("--per-page", type=int, default=100, help="Max zones to return.")

    records = sub.add_parser("records", help="DNS record operations.")
    records_sub = records.add_subparsers(dest="records_command", required=True)

    rec_list = records_sub.add_parser("list", help="List DNS records in a zone.")
    rec_list.add_argument("--zone", required=True, help="Zone id or exact zone name.")
    rec_list.add_argument("--name", default=None, help="Optional record name filter.")
    rec_list.add_argument("--type", default=None, help="Optional DNS type filter, e.g. A, CNAME, TXT.")
    rec_list.add_argument("--per-page", type=int, default=100, help="Max records to return.")

    rec_add = records_sub.add_parser("add", help="Create a new DNS record.")
    rec_add.add_argument("--zone", required=True, help="Zone id or exact zone name.")
    rec_add.add_argument("--type", required=True, help="DNS type, e.g. A, AAAA, CNAME, TXT.")
    rec_add.add_argument("--name", required=True, help="Record name, full or relative.")
    rec_add.add_argument("--content", required=True, help="Record value/content.")
    rec_add.add_argument("--ttl", type=int, default=1, help="TTL seconds (1 means auto).")
    rec_add.add_argument("--proxied", type=_bool_arg, default=None, help="Enable Cloudflare proxy.")
    rec_add.add_argument("--priority", type=int, default=None, help="MX/SRV priority when relevant.")
    rec_add.add_argument("--comment", default=None, help="Optional record comment.")

    rec_update = records_sub.add_parser("update", help="Update an existing DNS record.")
    rec_update.add_argument("--zone", required=True, help="Zone id or exact zone name.")
    rec_update.add_argument("--record-id", default=None, help="Record id (preferred if known).")
    rec_update.add_argument("--name", default=None, help="Record name (required with --type if no --record-id).")
    rec_update.add_argument(
        "--type",
        default=None,
        help="DNS type used for lookup when --record-id is not provided.",
    )
    rec_update.add_argument("--content", default=None, help="New content/value.")
    rec_update.add_argument("--ttl", type=int, default=None, help="New TTL seconds.")
    rec_update.add_argument("--proxied", type=_bool_arg, default=None, help="Set proxy state.")
    rec_update.add_argument("--priority", type=int, default=None, help="New priority value.")
    rec_update.add_argument("--comment", default=None, help="New comment.")

    rec_proxy = records_sub.add_parser("set-proxy", help="Turn orange cloud on/off for a record.")
    rec_proxy.add_argument("--zone", required=True, help="Zone id or exact zone name.")
    rec_proxy.add_argument("--record-id", default=None, help="Record id (preferred if known).")
    rec_proxy.add_argument("--name", default=None, help="Record name (required with --type if no --record-id).")
    rec_proxy.add_argument(
        "--type",
        default=None,
        help="DNS type used for lookup when --record-id is not provided.",
    )
    rec_proxy.add_argument(
        "--state",
        required=True,
        choices=("on", "off"),
        help="'on' enables proxy (orange cloud), 'off' disables it (gray cloud).",
    )
    return parser


def _print(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _run(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env)
    if not token:
        raise CloudflareError(
            f"Missing API token: set environment variable {args.token_env} before running command."
        )
    client = CloudflareClient(token=token)

    if args.command == "zones" and args.zones_command == "list":
        payload = client.request(
            "GET",
            "/zones",
            query={"name": args.name, "per_page": args.per_page},
        )
        _print(payload)
        return 0

    if args.command != "records":
        raise CloudflareError(f"Unsupported command: {args.command}")

    zone_id = client.resolve_zone_id(args.zone)

    if args.records_command == "list":
        payload = client.request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            query={"name": args.name, "type": args.type, "per_page": args.per_page},
        )
        _print(payload)
        return 0

    if args.records_command == "add":
        body: dict[str, Any] = {
            "type": args.type,
            "name": args.name,
            "content": args.content,
            "ttl": args.ttl,
        }
        if args.proxied is not None:
            body["proxied"] = args.proxied
        if args.priority is not None:
            body["priority"] = args.priority
        if args.comment is not None:
            body["comment"] = args.comment
        payload = client.request("POST", f"/zones/{zone_id}/dns_records", data=body)
        _print(payload)
        return 0

    if args.records_command == "update":
        record = client.find_record(
            zone_id=zone_id,
            record_id=args.record_id,
            name=args.name,
            record_type=args.type,
        )
        body: dict[str, Any] = {
            "type": record["type"],
            "name": record["name"],
            "content": record["content"],
            "ttl": record["ttl"],
            "proxied": record.get("proxied", False),
        }
        if "priority" in record and record["priority"] is not None:
            body["priority"] = record["priority"]
        if "comment" in record and record["comment"] is not None:
            body["comment"] = record["comment"]

        if args.content is not None:
            body["content"] = args.content
        if args.ttl is not None:
            body["ttl"] = args.ttl
        if args.proxied is not None:
            body["proxied"] = args.proxied
        if args.priority is not None:
            body["priority"] = args.priority
        if args.comment is not None:
            body["comment"] = args.comment

        payload = client.request("PUT", f"/zones/{zone_id}/dns_records/{record['id']}", data=body)
        _print(payload)
        return 0

    if args.records_command == "set-proxy":
        record = client.find_record(
            zone_id=zone_id,
            record_id=args.record_id,
            name=args.name,
            record_type=args.type,
        )
        body: dict[str, Any] = {
            "type": record["type"],
            "name": record["name"],
            "content": record["content"],
            "ttl": record["ttl"],
            "proxied": args.state == "on",
        }
        if "priority" in record and record["priority"] is not None:
            body["priority"] = record["priority"]
        if "comment" in record and record["comment"] is not None:
            body["comment"] = record["comment"]
        payload = client.request("PUT", f"/zones/{zone_id}/dns_records/{record['id']}", data=body)
        _print(payload)
        return 0

    raise CloudflareError(f"Unsupported records command: {args.records_command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except CloudflareError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
