# Mutual TLS and proxy boundaries

This page describes the unreleased 0.2 development implementation; the operational profile is not yet qualified.

Mutual TLS checks both ends of a connection using operator-provisioned certificates. It protects
transport and admits a connection; it does **not** assign mission roles or authorize a command.
CheckedFlow still requires a Bearer identity, its current [client policy](client-access.md), and the
appropriate signed command authority. Certificate subjects and forwarded headers never become roles.

## Configure the agent listener

For A2A or MCP HTTP, add all three arguments to your existing invocation:

```console
--tls-cert-file ./private/server-chain.pem --tls-key-file ./private/server-key.pem --tls-client-ca ./private/client-ca.pem
```

The server certificate must have appropriate server-auth usage and subject alternative names for
the hostname/IP the connecting client verifies. Clients must trust its issuing CA and present a
client-auth certificate from the configured client CA bundle. Issue separate credentials for
different owners and services. Protect the private key with OS ownership and permissions; never
mount it or the CA/policy directories into candidate workspaces. TLS keys are separate from validator,
application signing, OAuth verification and callback-encryption keys.

The CLI binds numeric loopback addresses as before. It does not open a public listener, install a
CA, change firewall rules or generate deployment credentials. Stdio rejects TLS configuration because
its boundary is process ownership. Missing/incomplete/invalid TLS settings fail closed before the
CLI contacts its node. PEM files are limited to 64 KiB each; password-encrypted private keys are not
accepted by this file-backed server adapter, and startup never prompts for a password. A supported
external TLS terminator may have its own protected key mechanism.

HTTP uses TLS 1.2 or later and requires verified client certificates. When A2A also enables its gRPC
listener, the **same startup snapshot** supplies its certificate, key and required client CA. HTTPS
URLs appear in the A2A card for secure listeners. The CLI also declares the standard mTLS security
scheme together with Bearer in one requirement (both are required, not alternatives). There is no switch from configured TLS to plaintext
after a certificate error. Omitting all TLS arguments retains the local development plaintext mode;
that mode is not an authenticated cross-host deployment.

The SDK boundary is explicit: `MutualTLS(certificate, key, client_ca)` creates the startup snapshot;
`http_config(app, host, port, tls)` supplies the Uvicorn settings used by both CLI servers. Passing
`grpc_tls=` to `create_app` secures its auxiliary gRPC listener only. An ASGI application alone cannot
encrypt its HTTP socket: run it with the shared HTTP configuration or a properly configured server.
For SDK-hosted A2A, explicitly set `advertise_mtls=True` only when the HTTP listener actually requires
client certificates. A discovery declaration cannot configure the hosting server.

## Reverse proxies

For A2A, `--advertised-url https://agent.example:8443/rpc` can name an operator-reviewed proxy address.
If the gRPC endpoint is separately proxied, supply `--grpc-advertised-url https://agent.example:8444`.
These overrides require TLS configuration (and an enabled gRPC listener for the gRPC override).
URLs cannot include credentials, query strings, fragments or arbitrary path prefixes. They are
advertisements, not instructions to create a proxy or open ports. For MCP OAuth, configure its
audience/resource-server URL to the externally intended HTTPS resource.

Both CLI HTTP servers explicitly disable Uvicorn proxy-header rewriting, including when
`FORWARDED_ALLOW_IPS=*` is present in the environment. Neither `Forwarded`, `X-Forwarded-For`,
`X-Forwarded-Proto` nor a client-certificate header confers identity. The proxy must preserve the
original Bearer header so the backend can verify the actual OAuth client; a proxy's own certificate
does not identify the end client. Do not replace client tokens with a shared privileged backend token.

The [Nginx example](../deploy/agent-proxy.conf.example) is a reviewable configuration starting point
for the A2A HTTP endpoint, with loopback-only listeners, required client certificates, authenticated
TLS to the backend, disabled access logs, bounded request bodies and streaming without buffering.
Supply real reviewed credentials and verify both hostname checks. For MCP, use a separate listener
and backend port, preserve `/mcp` and OAuth metadata paths, and match its advertised resource URL.
The example is not an installed service or evidence that a particular proxy deployment is qualified.
Proxy installation, gRPC forwarding, certificate issuance and multi-host routing still need the
maintained deployment profile and end-to-end deployment qualification.

## Rotation, revocation and recovery

Certificates, private key and client CA are captured at startup; the adapter checks that their files
did not change during loading. Update a protected credential set while stopped, then restart every
affected listener. Do not replace files piecemeal while a process starts. An existing process keeps
its original snapshot. Drain/stop old connections before declaring a client CA retired, including
connections and TLS sessions at a proxy. Tests demonstrate that a replacement listener accepts the
new client CA and rejects the retired one. Removing a CA file does not revoke an established session.

Client policy remains the immediate application-level inhibit for active streams and callbacks.
This adapter does not fetch CRLs/OCSP or implement hot certificate reload. Use short certificate
lifetimes and an explicit coordinated restart policy; urgent compromise requires withdrawing client
grants, stopping affected listeners and replacing trust as appropriate. Restore credentials, client
policy, callback custody and journals deliberately; restoring an old CA bundle or grant may restore
access. Full coordinated recovery remains a separate release requirement.

## Evidence

Tests perform real HTTP and gRPC TLS handshakes, checking missing, foreign and expired client
certificates, wrong server trust, valid certificates with missing Bearer identity, and live grant
withdrawal. They check CA replacement, startup corruption/change, CLI rejection before RPC, secure
A2A advertisements and forwarded-header spoofing. Five real transport cases also run against the
installed wheel in infrastructure qualification; source results alone do not prove that gate passed.

Implementation uses Uvicorn's SSL context factory and gRPC's required-client-auth credentials.
See [Uvicorn settings](https://www.uvicorn.org/settings/),
[Python SSL contexts](https://docs.python.org/3/library/ssl.html#ssl.SSLContext), and
[gRPC credentials](https://grpc.github.io/grpc/python/grpc.html#grpc.ssl_server_credentials).
These library mechanisms do not establish operator independence or complete G1-G7 qualification.
