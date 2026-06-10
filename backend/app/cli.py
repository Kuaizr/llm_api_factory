from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
from typing import Any, Iterable, Sequence

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class CLIError(RuntimeError):
    pass


@dataclass
class CommandResult:
    payload: Any
    rows: list[dict[str, Any]] | None = None
    columns: list[tuple[str, str]] | None = None


class FactoryClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FactoryClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self._client.request(
            method,
            path,
            json=json_body,
            params={key: value for key, value in (params or {}).items() if value is not None},
        )
        if response.status_code >= 400:
            detail: object
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise CLIError(f"{method} {path} failed: {response.status_code} {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _csv_int(value: str | None) -> list[int]:
    result: list[int] = []
    for item in _csv(value):
        try:
            result.append(int(item))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Invalid integer: {item}") from exc
    return result


def _key_value_pairs(values: Sequence[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise CLIError(f"Expected KEY=VALUE, got: {value}")
        key, item_value = value.split("=", 1)
        key = key.strip()
        if not key:
            raise CLIError(f"Empty key in pair: {value}")
        result[key] = item_value
    return result


def _model_pairs(values: Sequence[str] | None) -> list[tuple[str, str]]:
    pairs = []
    for value in values or []:
        if "=" not in value:
            raise CLIError(f"Expected ALIAS=REAL_MODEL, got: {value}")
        alias, real_model = value.split("=", 1)
        alias = alias.strip()
        real_model = real_model.strip()
        if not alias or not real_model:
            raise CLIError(f"Invalid model mapping: {value}")
        pairs.append((alias, real_model))
    return pairs


def _set_if_present(payload: dict[str, Any], args: argparse.Namespace, *fields: str) -> None:
    for field in fields:
        value = getattr(args, field, None)
        if value is not None:
            payload[field] = value


def _set_active_flag(payload: dict[str, Any], args: argparse.Namespace) -> None:
    if getattr(args, "active", False):
        payload["is_active"] = True
    if getattr(args, "inactive", False):
        payload["is_active"] = False


def _endpoint_payload(args: argparse.Namespace, *, update: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if not update:
        payload["name"] = args.name
        payload["base_url"] = args.base_url
    _set_if_present(
        payload,
        args,
        "name",
        "base_url",
        "provider",
        "strategy",
        "access_mode",
        "agent_node",
        "auth_header_name",
        "auth_header_prefix",
        "url_path_suffix",
        "probe_interval_seconds",
        "request_body_template",
    )
    _set_active_flag(payload, args)
    extra_headers = _key_value_pairs(getattr(args, "extra_header", None))
    extra_query_params = _key_value_pairs(getattr(args, "extra_query_param", None))
    if extra_headers:
        payload["extra_headers"] = extra_headers
    if extra_query_params:
        payload["extra_query_params"] = extra_query_params
    return payload


def _endpoint_rows(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "provider": item.get("provider"),
            "active": item.get("is_active"),
            "mode": item.get("access_mode"),
            "agent": item.get("agent_node"),
            "base_url": item.get("base_url"),
        }
        for item in items
    ]


def upstream_list(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = client.request("GET", "/admin/endpoints")
    rows = _endpoint_rows(payload if isinstance(payload, list) else [])
    return CommandResult(
        payload,
        rows=rows,
        columns=[
            ("id", "ID"),
            ("name", "Name"),
            ("provider", "Provider"),
            ("active", "Active"),
            ("mode", "Mode"),
            ("agent", "Agent"),
            ("base_url", "Base URL"),
        ],
    )


def upstream_add(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    endpoint = client.request("POST", "/admin/endpoints", json_body=_endpoint_payload(args))
    created: dict[str, Any] = {"endpoint": endpoint}
    endpoint_id = endpoint["id"]

    if args.key:
        key_payload: dict[str, Any] = {
            "key": args.key,
            "name": args.key_name,
            "rule_group": args.rule_group,
            "rule_groups": _csv(args.rule_groups) if args.rule_groups else None,
            "weight": args.weight,
        }
        key_payload = {key: value for key, value in key_payload.items() if value is not None}
        created["api_key"] = client.request(
            "POST", f"/admin/endpoints/{endpoint_id}/keys", json_body=key_payload
        )

    models = []
    for alias, real_model in _model_pairs(args.model):
        models.append(
            client.request(
                "POST",
                "/admin/model-maps",
                json_body={
                    "endpoint_id": endpoint_id,
                    "model_alias": alias,
                    "real_model": real_model,
                },
            )
        )
    if models:
        created["model_maps"] = models
    return CommandResult(created)


def upstream_update(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = _endpoint_payload(args, update=True)
    if not payload:
        raise CLIError("No endpoint fields to update")
    result = client.request("PATCH", f"/admin/endpoints/{args.endpoint_id}", json_body=payload)
    return CommandResult(result)


def upstream_disable(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    result = client.request(
        "PATCH",
        f"/admin/endpoints/{args.endpoint_id}",
        json_body={"is_active": False},
    )
    return CommandResult(result)


def upstream_enable(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    result = client.request(
        "PATCH",
        f"/admin/endpoints/{args.endpoint_id}",
        json_body={"is_active": True},
    )
    return CommandResult(result)


def upstream_test(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    result = client.request("POST", f"/admin/endpoints/{args.endpoint_id}/probe")
    return CommandResult(result)


def upstream_key_add(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload: dict[str, Any] = {
        "key": args.key,
        "name": args.name,
        "rule_group": args.rule_group,
        "rule_groups": _csv(args.rule_groups) if args.rule_groups else None,
        "weight": args.weight,
        "rpm_limit": args.rpm_limit,
        "daily_limit": args.daily_limit,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    result = client.request(
        "POST", f"/admin/endpoints/{args.endpoint_id}/keys", json_body=payload
    )
    return CommandResult(result)


def upstream_model_add(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    result = client.request(
        "POST",
        "/admin/model-maps",
        json_body={
            "endpoint_id": args.endpoint_id,
            "model_alias": args.model_alias,
            "real_model": args.real_model,
        },
    )
    return CommandResult(result)


def route_test_cmd(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = client.request(
        "POST",
        "/admin/route-test",
        json_body={"model": args.model, "rule_group": args.rule_group},
    )
    return CommandResult(
        payload,
        rows=payload.get("candidates", []),
        columns=[
            ("order", "#"),
            ("endpoint_id", "Endpoint"),
            ("endpoint_name", "Name"),
            ("api_key_id", "Key"),
            ("real_model", "Real Model"),
            ("execution_mode", "Mode"),
            ("agent_node", "Agent"),
        ],
    )


def route_explain_cmd(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = client.request(
        "POST",
        "/admin/route-explain",
        json_body={"model": args.model, "rule_group": args.rule_group},
    )
    rows = []
    for item in payload.get("candidates", []):
        row = dict(item)
        row["state"] = "selected" if row.get("selected") else "candidate"
        rows.append(row)
    for item in payload.get("excluded", []):
        row = dict(item)
        row["state"] = ",".join(item.get("reasons", []))
        rows.append(row)
    return CommandResult(
        payload,
        rows=rows,
        columns=[
            ("order", "#"),
            ("endpoint_id", "Endpoint"),
            ("endpoint_name", "Name"),
            ("api_key_id", "Key"),
            ("real_model", "Real Model"),
            ("execution_mode", "Mode"),
            ("agent_node", "Agent"),
            ("state", "State"),
        ],
    )


def _worker_rows(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "status": item.get("status"),
            "active": item.get("is_active"),
            "draining": item.get("is_draining"),
            "region": item.get("region"),
            "network_group": item.get("network_group"),
            "labels": ",".join(item.get("labels") or []),
            "last_seen_at": item.get("last_seen_at"),
        }
        for item in items
    ]


def worker_list(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = client.request("GET", "/admin/agents")
    return CommandResult(
        payload,
        rows=_worker_rows(payload if isinstance(payload, list) else []),
        columns=[
            ("id", "ID"),
            ("name", "Name"),
            ("status", "Status"),
            ("active", "Active"),
            ("draining", "Draining"),
            ("region", "Region"),
            ("network_group", "Network"),
            ("labels", "Labels"),
            ("last_seen_at", "Last Seen"),
        ],
    )


def worker_bootstrap(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    result = client.request(
        "POST", "/admin/agents/bootstrap", json_body={"name": args.name}
    )
    return CommandResult(result)


def worker_label(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload: dict[str, Any] = {}
    if args.labels is not None:
        payload["labels"] = _csv(args.labels)
    _set_if_present(payload, args, "region", "network_group", "endpoint_url")
    if not payload:
        raise CLIError("No worker labels or metadata to update")
    result = client.request("PATCH", f"/admin/agents/{args.agent_id}", json_body=payload)
    return CommandResult(result)


def worker_action(action: str):
    def _handler(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
        result = client.request("POST", f"/admin/agents/{args.agent_id}/{action}")
        return CommandResult(result)

    return _handler


def rule_group_list(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = client.request("GET", "/admin/rules")
    return CommandResult(
        payload,
        rows=payload if isinstance(payload, list) else [],
        columns=[
            ("id", "ID"),
            ("group_name", "Group"),
            ("model_pattern", "Pattern"),
            ("strategy", "Strategy"),
            ("is_active", "Active"),
            ("target_key_ids", "Keys"),
        ],
    )


def rule_group_create(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload = {
        "group_name": args.group,
        "model_pattern": args.model_pattern,
        "priority": args.priority,
        "strategy": args.strategy,
        "is_active": not args.inactive,
        "dump_enabled": args.dump_enabled,
        "dump_path": args.dump_path,
        "target_key_ids": _csv_int(args.key_ids),
    }
    result = client.request("POST", "/admin/rules", json_body=payload)
    return CommandResult(result)


def rule_group_update(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload: dict[str, Any] = {}
    if args.group is not None:
        payload["group_name"] = args.group
    if args.model_pattern is not None:
        payload["model_pattern"] = args.model_pattern
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.strategy is not None:
        payload["strategy"] = args.strategy
    if args.key_ids is not None:
        payload["target_key_ids"] = _csv_int(args.key_ids)
    if args.dump_enabled:
        payload["dump_enabled"] = True
    if args.dump_disabled:
        payload["dump_enabled"] = False
    if args.dump_path is not None:
        payload["dump_path"] = args.dump_path
    _set_active_flag(payload, args)
    if not payload:
        raise CLIError("No rule group fields to update")
    result = client.request("PATCH", f"/admin/rules/{args.rule_id}", json_body=payload)
    return CommandResult(result)


def rule_group_bind(args: argparse.Namespace, client: FactoryClient) -> CommandResult:
    payload: dict[str, Any] = {"target_key_ids": _csv_int(args.key_ids)}
    if args.strategy:
        payload["strategy"] = args.strategy
    result = client.request("PATCH", f"/admin/rules/{args.rule_id}", json_body=payload)
    return CommandResult(result)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _print_table(
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str]],
    stdout: Any,
) -> None:
    if not rows:
        print("No rows", file=stdout)
        return
    widths = []
    for key, header in columns:
        max_value = max(len(_stringify(row.get(key))) for row in rows)
        widths.append(max(len(header), max_value))
    header = "  ".join(label.ljust(width) for (_, label), width in zip(columns, widths))
    print(header, file=stdout)
    print("  ".join("-" * width for width in widths), file=stdout)
    for row in rows:
        print(
            "  ".join(
                _stringify(row.get(key)).ljust(width)
                for (key, _label), width in zip(columns, widths)
            ),
            file=stdout,
        )


def _emit(result: CommandResult, output: str, stdout: Any) -> None:
    if output == "json":
        json.dump(result.payload, stdout, ensure_ascii=False, indent=2)
        print(file=stdout)
        return
    if result.rows is not None and result.columns is not None:
        _print_table(result.rows, result.columns, stdout)
        return
    json.dump(result.payload, stdout, ensure_ascii=False, indent=2)
    print(file=stdout)


def _add_endpoint_flags(parser: argparse.ArgumentParser, *, update: bool = False) -> None:
    if update:
        parser.add_argument("--name")
        parser.add_argument("--base-url")
    else:
        parser.add_argument("name")
        parser.add_argument("base_url")
    parser.add_argument("--provider", choices=["openai", "anthropic", "gemini", "custom"])
    parser.add_argument("--strategy", choices=["weighted_round_robin", "sequential"])
    parser.add_argument("--access-mode", choices=["direct", "via_agent"])
    parser.add_argument("--agent-node")
    parser.add_argument("--auth-header-name")
    parser.add_argument("--auth-header-prefix")
    parser.add_argument("--url-path-suffix")
    parser.add_argument("--probe-interval-seconds", type=int)
    parser.add_argument("--request-body-template")
    active_group = parser.add_mutually_exclusive_group()
    active_group.add_argument("--active", action="store_true")
    active_group.add_argument("--inactive", action="store_true")
    parser.add_argument("--extra-header", action="append", metavar="KEY=VALUE")
    parser.add_argument("--extra-query-param", action="append", metavar="KEY=VALUE")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-factory",
        description="Automation CLI for llm_api_factory.",
    )
    parser.add_argument(
        "--base-url",
        dest="control_base_url",
        default=os.getenv("LLM_FACTORY_URL", DEFAULT_BASE_URL),
        help="Control-plane base URL. Env: LLM_FACTORY_URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("LLM_FACTORY_TOKEN") or os.getenv("LLM_MASTER_AUTH_TOKEN"),
        help="Admin token. Env: LLM_FACTORY_TOKEN or LLM_MASTER_AUTH_TOKEN",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", choices=["table", "json"], default="table")

    commands = parser.add_subparsers(dest="command", required=True)

    upstream = commands.add_parser("upstream", help="Manage upstream endpoints.")
    upstream_commands = upstream.add_subparsers(dest="upstream_command", required=True)
    upstream_list_parser = upstream_commands.add_parser("list")
    upstream_list_parser.set_defaults(func=upstream_list)

    upstream_add_parser = upstream_commands.add_parser("add")
    _add_endpoint_flags(upstream_add_parser)
    upstream_add_parser.add_argument("--key")
    upstream_add_parser.add_argument("--key-name")
    upstream_add_parser.add_argument("--rule-group", default="default")
    upstream_add_parser.add_argument("--rule-groups")
    upstream_add_parser.add_argument("--weight", type=int, default=1)
    upstream_add_parser.add_argument("--model", action="append", metavar="ALIAS=REAL")
    upstream_add_parser.set_defaults(func=upstream_add)

    upstream_update_parser = upstream_commands.add_parser("update")
    upstream_update_parser.add_argument("endpoint_id", type=int)
    _add_endpoint_flags(upstream_update_parser, update=True)
    upstream_update_parser.set_defaults(func=upstream_update)

    for name, handler in (("disable", upstream_disable), ("enable", upstream_enable), ("test", upstream_test)):
        command_parser = upstream_commands.add_parser(name)
        command_parser.add_argument("endpoint_id", type=int)
        command_parser.set_defaults(func=handler)

    key_add_parser = upstream_commands.add_parser("key-add")
    key_add_parser.add_argument("endpoint_id", type=int)
    key_add_parser.add_argument("key")
    key_add_parser.add_argument("--name")
    key_add_parser.add_argument("--rule-group", default="default")
    key_add_parser.add_argument("--rule-groups")
    key_add_parser.add_argument("--weight", type=int, default=1)
    key_add_parser.add_argument("--rpm-limit", type=int)
    key_add_parser.add_argument("--daily-limit", type=int)
    key_add_parser.set_defaults(func=upstream_key_add)

    model_add_parser = upstream_commands.add_parser("model-add")
    model_add_parser.add_argument("endpoint_id", type=int)
    model_add_parser.add_argument("model_alias")
    model_add_parser.add_argument("real_model")
    model_add_parser.set_defaults(func=upstream_model_add)

    route = commands.add_parser("route", help="Test and explain routing.")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    for name, handler in (("test", route_test_cmd), ("explain", route_explain_cmd)):
        route_parser = route_commands.add_parser(name)
        route_parser.add_argument("model")
        route_parser.add_argument("--rule-group", default="default")
        route_parser.set_defaults(func=handler)

    worker = commands.add_parser("worker", help="Manage agent workers.")
    worker_commands = worker.add_subparsers(dest="worker_command", required=True)
    worker_list_parser = worker_commands.add_parser("list")
    worker_list_parser.set_defaults(func=worker_list)
    worker_bootstrap_parser = worker_commands.add_parser("bootstrap")
    worker_bootstrap_parser.add_argument("name")
    worker_bootstrap_parser.set_defaults(func=worker_bootstrap)
    worker_label_parser = worker_commands.add_parser("label")
    worker_label_parser.add_argument("agent_id", type=int)
    worker_label_parser.add_argument("--labels")
    worker_label_parser.add_argument("--region")
    worker_label_parser.add_argument("--network-group")
    worker_label_parser.add_argument("--endpoint-url")
    worker_label_parser.set_defaults(func=worker_label)
    for action in ("drain", "enable", "disable"):
        action_parser = worker_commands.add_parser(action)
        action_parser.add_argument("agent_id", type=int)
        action_parser.set_defaults(func=worker_action(action))

    rule_group = commands.add_parser("rule-group", help="Manage route policies.")
    rule_commands = rule_group.add_subparsers(dest="rule_command", required=True)
    rule_list_parser = rule_commands.add_parser("list")
    rule_list_parser.set_defaults(func=rule_group_list)

    rule_create_parser = rule_commands.add_parser("create")
    rule_create_parser.add_argument("group")
    rule_create_parser.add_argument("model_pattern")
    rule_create_parser.add_argument("--key-ids", default="")
    rule_create_parser.add_argument("--strategy", default="weighted_round_robin")
    rule_create_parser.add_argument("--priority", type=int, default=10)
    rule_create_parser.add_argument("--inactive", action="store_true")
    rule_create_parser.add_argument("--dump-enabled", action="store_true")
    rule_create_parser.add_argument("--dump-path")
    rule_create_parser.set_defaults(func=rule_group_create)

    rule_update_parser = rule_commands.add_parser("update")
    rule_update_parser.add_argument("rule_id", type=int)
    rule_update_parser.add_argument("--group")
    rule_update_parser.add_argument("--model-pattern")
    rule_update_parser.add_argument("--key-ids")
    rule_update_parser.add_argument("--strategy")
    rule_update_parser.add_argument("--priority", type=int)
    active_group = rule_update_parser.add_mutually_exclusive_group()
    active_group.add_argument("--active", action="store_true")
    active_group.add_argument("--inactive", action="store_true")
    dump_group = rule_update_parser.add_mutually_exclusive_group()
    dump_group.add_argument("--dump-enabled", action="store_true")
    dump_group.add_argument("--dump-disabled", action="store_true")
    rule_update_parser.add_argument("--dump-path")
    rule_update_parser.set_defaults(func=rule_group_update)

    rule_bind_parser = rule_commands.add_parser("bind")
    rule_bind_parser.add_argument("rule_id", type=int)
    rule_bind_parser.add_argument("--key-ids", required=True)
    rule_bind_parser.add_argument("--strategy")
    rule_bind_parser.set_defaults(func=rule_group_bind)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: Any = None,
    stderr: Any = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with FactoryClient(
            args.control_base_url,
            args.token,
            timeout=args.timeout,
            transport=transport,
        ) as client:
            result = args.func(args, client)
        _emit(result, args.output, stdout)
        return 0
    except CLIError as exc:
        print(str(exc), file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
