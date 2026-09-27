"""Build the checked-in schema and command catalogue from one small normative definition."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/checkedflow/data"
S = {"type": "string", "minLength": 1, "maxLength": 256}
INTEGER = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
N = INTEGER | {"minimum": 1}
B = {"type": "boolean"}
OBJECT = {"type": "object"}
NAMES = {"type": "array", "items": S, "uniqueItems": True, "maxItems": 64}
IMAGE = S | {"pattern": r"^.+@sha256:[0-9a-f]{64}$"}


def closed(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


PAYLOADS = {
    "worker.register": {
        "id": S,
        "key": S | {"pattern": "^[0-9a-f]{64}$"},
        "organization": S,
        "roles": {
            "type": "array",
            "items": {"enum": ["producer", "verifier", "executor"]},
            "minItems": 1,
            "uniqueItems": True,
            "maxItems": 3,
        },
    },
    "worker.revoke": {"id": S, "reason": S},
    "verifier.register": {
        "id": S,
        "contract": S,
        "image": IMAGE,
        "argv": {"type": "array", "items": S, "minItems": 1, "maxItems": 32},
        "exhaustive": B,
    },
    "mission.create": {
        "id": S,
        "workers": NAMES,
        "budget": N,
        "verification_reserve": N,
        "verification_cost": N,
        "expires": N,
        "max_rounds": N | {"maximum": 16},
        "max_candidates": N | {"maximum": 256},
        "max_depth": N | {"maximum": 16},
        "max_attempts": N | {"maximum": 16},
        "verifier": S,
        "receiver": S,
        "contract": S,
        "image": IMAGE,
    },
    "task.create": {
        "id": S,
        "mission": S,
        "phase": {"enum": ["generate", "execute", "repair"]},
        "spec": OBJECT,
        "dependencies": NAMES | {"maxItems": 16},
        "cost": N,
        "ttl": N | {"maximum": 10000},
        "effect": {"enum": ["isolated", "external"]},
    },
    "task.lease": {"id": S},
    "task.start": {"id": S, "fence": N},
    "task.heartbeat": {"id": S, "fence": N},
    "task.finish": {
        "id": S,
        "fence": N,
        "result": closed(
            {"outcome": {"enum": ["reported", "pass", "fail", "unknown"]}, "evidence": OBJECT}
        ),
    },
    "task.reconcile": {"id": S, "retry": B, "reason": S | {"maxLength": 4096}},
    "capability.propose": {
        "id": S,
        "task": S,
        "source": S | {"maxLength": 262144},
        "expires": N,
        "origin": {"enum": ["generated", "external"]},
    },
    "capability.revoke": {"id": S, "reason": S | {"maxLength": 4096}},
    "residual.resolve": {"id": S, "evidence": S, "reason": S | {"maxLength": 4096}},
}


def artifacts() -> dict[str, dict[str, object]]:
    branches = [
        closed(
            {
                "api_version": {"const": "checkedflow/v1"},
                "chain": S,
                "id": S,
                "actor": S,
                "nonce": N,
                "kind": {"const": name},
                "payload": closed(payload),
            }
        )
        for name, payload in PAYLOADS.items()
    ]
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:checkedflow:envelope:v1",
        **closed(
            {
                "command": {"oneOf": branches},
                "signatures": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": closed({"signer": S, "signature": S | {"pattern": "^[0-9a-f]{128}$"}}),
                },
            }
        ),
    }
    catalogue = {
        "api_version": "checkedflow/v1",
        "commands": [
            {
                "kind": name,
                "authority": "three_organizations"
                if name
                in {
                    "worker.register",
                    "worker.revoke",
                    "verifier.register",
                    "mission.create",
                    "task.reconcile",
                    "capability.revoke",
                    "residual.resolve",
                }
                else "mission_worker",
                "mutates_state": True,
                "executes_code": False,
                "payload_fields": list(payload),
            }
            for name, payload in PAYLOADS.items()
        ],
    }
    string = {"type": "string"}
    strings = {"type": "array", "items": string}
    models = {
        "worker": {"key": S, "organization": S, "roles": strings, "revoked": B},
        "verifier": {"contract": S, "image": IMAGE, "argv": strings, "exhaustive": B},
        "mission": {
            **dict.fromkeys(["workers"], strings),
            **dict.fromkeys(
                [
                    "budget",
                    "verification_reserve",
                    "verification_cost",
                    "expires",
                    "max_rounds",
                    "max_candidates",
                    "max_depth",
                    "max_attempts",
                    "spent",
                    "reserved",
                    "candidates",
                    "uses",
                ],
                INTEGER,
            ),
            **dict.fromkeys(["verifier", "receiver", "contract", "image"], S),
        },
        "task": {
            **dict.fromkeys(
                ["mission", "phase", "effect", "organization", "subject", "status", "owner"], string
            ),
            **dict.fromkeys(["cost", "ttl", "fence", "deadline", "attempts"], INTEGER),
            "funded": B,
            "dependencies": strings,
            "spec": OBJECT,
            "result": OBJECT,
        },
        "capability": {
            **dict.fromkeys(
                ["mission", "source", "source_digest", "task", "status", "behavior", "origin"],
                string,
            ),
            **dict.fromkeys(["round", "depth", "expires"], INTEGER),
            "dependencies": strings,
            "votes": OBJECT,
        },
        "residual": dict.fromkeys(["subject", "reason", "trigger", "status", "resolution"], string),
    }
    state = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:checkedflow:state:v1",
        "$defs": {name: closed(properties) for name, properties in models.items()},
        **closed(
            {
                "chain": S,
                "height": INTEGER,
                "organizations": {
                    "type": "object",
                    "minProperties": 4,
                    "maxProperties": 4,
                    "additionalProperties": S | {"pattern": "^[0-9a-f]{64}$"},
                },
                **{
                    collection: {
                        "type": "object",
                        "additionalProperties": {"$ref": f"#/$defs/{name}"},
                    }
                    for collection, name in {
                        "workers": "worker",
                        "verifiers": "verifier",
                        "missions": "mission",
                        "tasks": "task",
                        "capabilities": "capability",
                        "residuals": "residual",
                    }.items()
                },
                "processed": {"type": "object", "additionalProperties": S},
                "nonces": {"type": "object", "additionalProperties": INTEGER},
            }
        ),
    }
    generator = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:checkedflow:generator:v1",
        **closed(
            {
                "target": strings | {"maxItems": 4},
                "max_candidates": N | {"maximum": 4096},
                "max_depth": N | {"maximum": 4},
                "library": {
                    "type": "array",
                    "maxItems": 16,
                    "items": closed({"id": S, "operations": strings | {"maxItems": 4}}),
                },
            }
        ),
    }
    return {
        "agent-vectors.json": {
            "profile": "checkedflow-agents/v1",
            "scope": "data-part shape and task projection; not envelope authentication",
            "requests": [
                {"input": {"operation": "profile"}, "valid": True},
                {
                    "input": {"operation": "inspect", "kind": "mission", "identity": ""},
                    "valid": True,
                },
                {"input": {"operation": "submit", "envelopeJson": "{}"}, "valid": True},
                {"input": {"operation": "task", "envelopeJson": "{}"}, "valid": True},
                {"input": {"operation": "submit", "envelopeJson": {}}, "valid": False},
                {"input": {"operation": "profile", "extra": True}, "valid": False},
                {"input": {"operation": "inspect", "kind": "task"}, "valid": False},
                {"input": {"operation": "inspect", "kind": "task", "identity": 1}, "valid": False},
                {
                    "input": {"operation": "inspect", "kind": "worker", "identity": "w0"},
                    "valid": False,
                },
            ],
            "projections": [
                {"status": status, "outcome": outcome, "a2a": "TASK_STATE_" + expected}
                for status, outcome, expected in [
                    ("ready", "", "SUBMITTED"),
                    ("leased", "", "WORKING"),
                    ("running", "", "WORKING"),
                    ("uncertain", "unknown", "INPUT_REQUIRED"),
                    ("abandoned", "unknown", "CANCELED"),
                    ("finished", "reported", "COMPLETED"),
                    ("finished", "fail", "FAILED"),
                ]
            ],
        },
        "agents.json": {
            "profile": "checkedflow-agents/v1",
            "command_protocol": "checkedflow/v1",
            "scope": "one configured chain and mission per gateway",
            "signed_transport": "UTF-8 JSON string; parse strictly before authenticating",
            "max_signed_bytes": 1048576,
            "a2a": {
                "version": "1.0",
                "bindings": ["JSONRPC", "HTTP+JSON", "GRPC"],
                "card": "/.well-known/agent-card.json",
                "endpoint": "/rpc",
                "authentication": "operator bearer token; loopback or authenticated tunnel",
                "methods": [
                    "SendMessage",
                    "SendStreamingMessage",
                    "GetTask",
                    "ListTasks",
                    "CancelTask",
                    "SubscribeToTask",
                    "CreateTaskPushNotificationConfig",
                    "GetTaskPushNotificationConfig",
                    "ListTaskPushNotificationConfigs",
                    "DeleteTaskPushNotificationConfig",
                    "GetExtendedAgentCard",
                ],
                "send_response": "submit/profile/inspect: direct Message; task: tracked Task",
                "data_schema": "agent-request.schema.json",
                "history": "at most 64 durable observed updates; default zero; not consensus time",
                "pagination": "authenticated cursor; descending observed time; restart if changed",
                "push": "allowlisted HTTPS; public DNS pinning; three attempts then reconfigure",
                "tenant": "empty or the configured mission; no cross-mission routing",
                "cancellation": "uncertain task; metadata.envelopeJson: signed abandonment",
                "callback_secrets": {
                    "storage": "AES-256-GCM; operator keyring required for persistent callbacks",
                    "responses": "token and authentication.credentials omitted",
                    "rotation": "explicit atomic Journal.rewrap; stop other writers",
                    "schema": "callback-keyring.schema.json",
                },
            },
            "mcp": {
                "version": "2026-07-28",
                "transports": ["stdio", "streamable-http", "legacy-sse"],
                "legacy": "initialize handshake supported by the official SDK",
                "tools": ["checkedflow_inspect", "checkedflow_submit"],
                "resources": [
                    "checkedflow://profile",
                    "checkedflow://mission",
                    "checkedflow://schemas/envelope",
                ],
                "resource_templates": [
                    "checkedflow://tasks/{identity}",
                    "checkedflow://capabilities/{identity}",
                    "checkedflow://residuals/{identity}",
                ],
                "prompts": ["checkedflow_review"],
                "completion": "mission-visible identity prefixes for templates and review prompt",
                "notifications": "subscriptions/listen resource updates; disconnect then refetch",
                "authentication": "stdio process ownership; local bearer or OAuth resource server",
            },
            "inspection_kinds": ["mission", "tasks", "task", "capability", "residual"],
            "submit_kinds": [
                "task.create",
                "task.lease",
                "task.start",
                "task.heartbeat",
                "task.finish",
                "task.reconcile",
                "capability.propose",
                "capability.revoke",
                "residual.resolve",
            ],
            "errors": {
                "OUTCOME_UNKNOWN": "submission may have committed; inspect before retrying",
                "REJECTED": "node reported rejection; do not retry unchanged conditions",
                "UNAVAILABLE": "no state observation; no execution authority inferred",
                "SCOPE": "wrong mission or unsupported global governance",
                "NOT_FOUND": "record absent, outside scope or ambiguous",
                "other": "underlying CheckedFlow contract error; no implicit permission",
            },
            "guarantees_not_provided": [
                "arbitrary remote-node trust",
                "signing",
                "code execution",
                "truth verification",
                "consensus wall-clock task history",
                "general A2A message interpretation",
            ],
        },
        "agent-request.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:checkedflow:agent-request:v1",
            "oneOf": [
                closed({"operation": {"const": "profile"}}),
                closed(
                    {
                        "operation": {"enum": ["submit", "task"]},
                        "envelopeJson": {"type": "string", "minLength": 1, "maxLength": 1048576},
                    }
                ),
                closed(
                    {
                        "operation": {"const": "inspect"},
                        "kind": {"enum": ["mission", "tasks", "task", "capability", "residual"]},
                        "identity": {"type": "string", "maxLength": 256},
                    }
                ),
            ],
        },
        "envelope.schema.json": schema,
        "commands.json": catalogue,
        "state.schema.json": state,
        "generator.schema.json": generator,
        "block.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:checkedflow:block:v1",
            **closed(
                {
                    "height": N,
                    "transactions": {
                        "type": "array",
                        "maxItems": 2097152,
                        "items": closed(
                            {
                                "raw": {
                                    "type": "string",
                                    "maxLength": 2097152,
                                    "pattern": "^(?:[0-9a-f]{2})*$",
                                },
                                "code": S,
                            }
                        ),
                    },
                }
            ),
        },
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts().items():
        (OUT / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
