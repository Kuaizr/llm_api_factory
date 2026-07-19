from __future__ import annotations

from io import StringIO
import json

import httpx

from app import cli


def run_cli(argv: list[str], handler) -> tuple[int, str, str, list[dict]]:
    requests: list[dict] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}") if request.content else None
        requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": body,
            }
        )
        return handler(request, body)

    stdout = StringIO()
    stderr = StringIO()
    code = cli.main(
        ["--base-url", "http://factory", "--token", "admin", "--output", "json", *argv],
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(transport_handler),
    )
    return code, stdout.getvalue(), stderr.getvalue(), requests


def json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_cli_upstream_add_can_create_key_and_model_map() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/admin/endpoints":
            assert body == {
                "name": "openai-vps",
                "base_url": "https://api.openai.com/v1",
                "provider": "openai",
                "access_mode": "via_agent",
                "agent_node": "edge-vps",
            }
            return json_response({"id": 7, "name": "openai-vps"})
        if request.method == "POST" and request.url.path == "/admin/endpoints/7/keys":
            assert body == {
                "key": "sk-test",
                "rule_group": "vps",
                "weight": 3,
            }
            return json_response({"id": 11})
        if request.method == "POST" and request.url.path == "/admin/model-maps":
            assert body == {
                "endpoint_id": 7,
                "model_alias": "gpt-vps",
                "real_model": "gpt-4.1",
            }
            return json_response({"id": 13})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    code, stdout, stderr, requests = run_cli(
        [
            "upstream",
            "add",
            "openai-vps",
            "https://api.openai.com/v1",
            "--provider",
            "openai",
            "--access-mode",
            "via_agent",
            "--agent-node",
            "edge-vps",
            "--key",
            "sk-test",
            "--rule-group",
            "vps",
            "--weight",
            "3",
            "--model",
            "gpt-vps=gpt-4.1",
        ],
        handler,
    )

    assert code == 0
    assert stderr == ""
    assert len(requests) == 3
    assert json.loads(stdout)["model_maps"][0]["id"] == 13


def test_cli_route_explain_calls_route_explain_api() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/admin/route-explain"
        assert body == {"model": "gpt-vps", "rule_group": "vps"}
        return json_response(
            {
                "model": "gpt-vps",
                "requested_rule_group": "vps",
                "effective_rule_group": "vps",
                "fallback_used": False,
                "strategy": "sequential",
                "target_key_ids": [1],
                "candidates": [],
                "excluded": [],
                "notes": [],
            }
        )

    code, stdout, stderr, requests = run_cli(
        ["route", "explain", "gpt-vps", "--rule-group", "vps"],
        handler,
    )

    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/route-explain"
    assert json.loads(stdout)["strategy"] == "sequential"


def test_cli_upstream_list_update_disable_and_test() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/admin/endpoints":
            return json_response(
                [
                    {
                        "id": 1,
                        "name": "openai",
                        "provider": "openai",
                        "is_active": True,
                        "access_mode": "direct",
                        "agent_node": None,
                        "base_url": "https://api.openai.com/v1",
                    }
                ]
            )
        if request.method == "PATCH" and request.url.path == "/admin/endpoints/1":
            return json_response({"id": 1, **(body or {})})
        if request.method == "POST" and request.url.path == "/admin/endpoints/1/probe":
            return json_response({"provider": "openai", "probe_status": "success"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    code, stdout, stderr, requests = run_cli(["upstream", "list"], handler)
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)[0]["name"] == "openai"

    code, _stdout, stderr, requests = run_cli(
        ["upstream", "update", "1", "--name", "openai-new", "--inactive"],
        handler,
    )
    assert code == 0
    assert stderr == ""
    assert requests[0]["body"] == {"name": "openai-new", "is_active": False}

    code, _stdout, stderr, requests = run_cli(["upstream", "disable", "1"], handler)
    assert code == 0
    assert stderr == ""
    assert requests[0]["body"] == {"is_active": False}

    code, stdout, stderr, requests = run_cli(["upstream", "test", "1"], handler)
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["probe_status"] == "success"


def test_cli_route_test_calls_route_test_api() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/admin/route-test"
        assert body == {"model": "gpt-vps", "rule_group": "vps"}
        return json_response(
            {
                "model": "gpt-vps",
                "rule_group": "vps",
                "candidates": [
                    {
                        "order": 1,
                        "endpoint_id": 1,
                        "endpoint_name": "openai",
                        "api_key_id": 2,
                        "weight": 1,
                        "real_model": "gpt-4.1",
                        "execution_mode": "direct",
                        "agent_node": None,
                    }
                ],
            }
        )

    code, stdout, stderr, requests = run_cli(
        ["route", "test", "gpt-vps", "--rule-group", "vps"],
        handler,
    )

    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/route-test"
    assert json.loads(stdout)["candidates"][0]["real_model"] == "gpt-4.1"


def test_cli_worker_label_and_drain() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        if request.method == "PATCH" and request.url.path == "/admin/agents/5":
            assert body == {
                "labels": ["openai", "restricted"],
                "region": "us",
                "network_group": "egress-us",
            }
            return json_response({"id": 5, "labels": ["openai", "restricted"]})
        if request.method == "POST" and request.url.path == "/admin/agents/5/drain":
            return json_response({"id": 5, "is_active": False})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    code, _stdout, stderr, requests = run_cli(
        [
            "worker",
            "label",
            "5",
            "--labels",
            "openai,restricted",
            "--region",
            "us",
            "--network-group",
            "egress-us",
        ],
        handler,
    )
    assert code == 0
    assert stderr == ""

    code, _stdout, stderr, requests = run_cli(["worker", "drain", "5"], handler)
    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/agents/5/drain"


def test_cli_worker_list_enable_and_disable() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/admin/agents":
            return json_response(
                [
                    {
                        "id": 5,
                        "name": "edge-vps",
                        "status": "online",
                        "is_active": True,
                        "region": "us",
                        "network_group": "egress-us",
                        "labels": ["openai"],
                        "last_seen_at": "2024-01-01T00:00:00Z",
                    }
                ]
            )
        if request.method == "POST" and request.url.path == "/admin/agents/5/enable":
            return json_response({"id": 5, "is_active": True})
        if request.method == "POST" and request.url.path == "/admin/agents/5/disable":
            return json_response({"id": 5, "is_active": False})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    code, stdout, stderr, requests = run_cli(["worker", "list"], handler)
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)[0]["name"] == "edge-vps"

    code, _stdout, stderr, requests = run_cli(["worker", "enable", "5"], handler)
    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/agents/5/enable"

    code, _stdout, stderr, requests = run_cli(["worker", "disable", "5"], handler)
    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/agents/5/disable"


def test_cli_rule_group_bind_calls_patch_rule() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/admin/rules/9"
        assert body == {"target_key_ids": [1, 2, 3], "strategy": "sequential"}
        return json_response({"id": 9, "target_key_ids": [1, 2, 3]})

    code, stdout, stderr, requests = run_cli(
        [
            "rule-group",
            "bind",
            "9",
            "--key-ids",
            "1,2,3",
            "--strategy",
            "sequential",
        ],
        handler,
    )

    assert code == 0
    assert stderr == ""
    assert requests[0]["path"] == "/admin/rules/9"
    assert json.loads(stdout)["target_key_ids"] == [1, 2, 3]


def test_cli_rule_group_create_and_update() -> None:
    def handler(request: httpx.Request, body: object) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/admin/rules":
            assert body == {
                "group_name": "vps",
                "model_pattern": "^gpt-vps$",
                "exposure_formats": ["codex", "response"],
                "priority": 50,
                "strategy": "sequential",
                "is_active": True,
                "dump_enabled": False,
                "dump_path": None,
                "target_key_ids": [1, 2],
            }
            return json_response({"id": 9, "group_name": "vps"})
        if request.method == "PATCH" and request.url.path == "/admin/rules/9":
            assert body == {
                "model_pattern": "^gpt-.*$",
                "target_key_ids": [2],
                "is_active": False,
            }
            return json_response({"id": 9, **(body or {})})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    code, stdout, stderr, requests = run_cli(
        [
            "rule-group",
            "create",
            "vps",
            "^gpt-vps$",
            "--key-ids",
            "1,2",
            "--strategy",
            "sequential",
            "--exposure-formats",
            "codex,response",
            "--priority",
            "50",
        ],
        handler,
    )
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["group_name"] == "vps"

    code, _stdout, stderr, requests = run_cli(
        [
            "rule-group",
            "update",
            "9",
            "--model-pattern",
            "^gpt-.*$",
            "--key-ids",
            "2",
            "--inactive",
        ],
        handler,
    )
    assert code == 0
    assert stderr == ""
    assert requests[0]["body"]["is_active"] is False
