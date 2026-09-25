"""An offline SDK example with ephemeral keys; no generated code executes."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow import genesis
from checkedflow.identity import public_key, sign
from checkedflow.runtime import Runtime
from checkedflow.wire import dumps


def main() -> None:
    organizations = {f"org{i}": Ed25519PrivateKey.generate() for i in range(4)}
    worker = Ed25519PrivateKey.generate()
    initial = genesis("sdk-example", {name: public_key(key) for name, key in organizations.items()})
    envelope = sign(
        {
            "api_version": "checkedflow/v1",
            "chain": "sdk-example",
            "id": "registration-1",
            "actor": "org0",
            "nonce": 1,
            "kind": "worker.register",
            "payload": {
                "id": "worker0",
                "key": public_key(worker),
                "organization": "org0",
                "roles": ["producer", "executor", "verifier"],
            },
        },
        {name: organizations[name] for name in ("org0", "org1", "org2")},
    )
    first, second = Runtime(initial), Runtime(initial)
    first.apply(envelope, height=1)
    second.apply(envelope, height=1)
    assert first.state_hash == second.state_hash  # nosec B101
    print(
        dumps(
            {
                "registered_worker": "worker0",
                "height": first.state.height,
                "replay_matches": first.state_hash == second.state_hash,
                "state_hash": first.state_hash,
            }
        ).decode("utf-8")
    )


if __name__ == "__main__":
    main()
