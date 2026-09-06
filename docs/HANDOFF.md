# Project handoff

> Start here for the current MVP. The authoritative scope is
> [`docs/integration-api-scope.md`](integration-api-scope.md); optional research
> is clearly separated below.

## 1. Project objective

The implemented Integration Layer is:

```text
CloudStack client
  -> authenticated REST API
  -> Python container on BlueField
  -> allowlisted eswitchctl
  -> existing eswitch-management daemon
```

This repository does not modify Apache CloudStack, the KVM Agent, Libvirt, or
the DOCA data plane.

## 2. Responsibility boundary

| Owner | Responsibilities |
|---|---|
| Integration API | Bearer authentication, request validation, exact REST-to-`eswitchctl` translation, output parsing, normalized responses, timeout/error handling, health/readiness, and secure container deployment |
| CloudStack team | API invocation timing, VM/NIC lifecycle, port-selection policy, host/PF/VF-to-PCI mapping, PCI passthrough, persistence, retries, and rollback |
| DOCA/eSwitch team | `eswitch-management`, DOCA/DPDK state, Unix-socket protocol, CLI semantics, hardware errors, and daemon behavior |

## 3. Implemented REST API

All `/api/v1/*` routes require Bearer authentication. Health routes are public.

| Method and path | Request body | Exact CLI arguments | Success | Type |
|---|---|---|---|---|
| `POST /api/v1/vswitches` | `{"vswitch_id": ID}` | `vs-create --id ID` | 201 | Mutation |
| `DELETE /api/v1/vswitches/{vswitch_id}` | None | `vs-delete --id ID` | 204 | Mutation |
| `GET /api/v1/vswitches` | None | `vs-list` | 200 | Observation |
| `GET /api/v1/ports/available` | None | `list-port-available` | 200 | Observation |
| `POST /api/v1/vswitches/{vswitch_id}/ports` | `{"port_id": PORT}` | `vs-port-attach --id ID --port PORT` | 200 | Mutation |
| `DELETE /api/v1/vswitches/{vswitch_id}/ports/{port_id}` | None | `vs-port-detach --id ID --port PORT` | 204 | Mutation |
| `GET /health/live` | None | None | 200 | Process health |
| `GET /health/ready` | None | `status` in CLI mode | 200 or 503 | Dependency observation |

The optional atomic-allocation endpoint is mock-only, fail-closed in CLI mode,
and outside current production scope. The API has no arbitrary command or
`show-fdb` endpoint.

## 4. Port identity contract

Available ports normalize to:

| Field | Meaning |
|---|---|
| `port_id` | Current DPDK runtime port identity |
| `type` | `representor` or `uplink` |
| `host` | Stable host identity for a representor; null for uplink |
| `pf` | Stable PF identity for a representor; null for uplink |
| `vf_index` | Stable VF index for a representor; null for uplink |

DPDK port IDs may change after daemon restart and must be refreshed.
`host`/`pf`/`vf_index` is the stable mapping identity consumed by CloudStack.
The Integration API does not resolve PCI addresses or decide VM attachment
safety. Port 0 is the uplink, not a VF passthrough candidate; this project does
not automatically attach it.

`GET /api/v1/vswitches` reports current membership. Membership is observation,
not ownership authorization or proof that detach is safe.

## 5. Runtime architecture

- Python container targeting `linux/arm64` on BlueField.
- Runtime image: `python:3.12-slim-bookworm`.
- CLI adapter executes mounted `/usr/local/bin/eswitchctl` with argument lists
  and `shell=False`.
- `/run/eswitch-management` is mounted read-only so a recreated socket remains
  visible.
- `eswitch-management` is an independent container and is never managed by the
  API or its Compose project.
- Runtime UID/GID is 10001; supplementary group 0 is a PoC socket-access
  accommodation.
- Root filesystem is read-only, `/tmp` is a 16 MiB tmpfs, all capabilities are
  dropped, and no-new-privileges is enabled.
- No privileged mode, host networking, Docker socket, hugepages, host devices,
  or daemon configuration mounts are used.

Production should replace supplementary group 0 with a dedicated socket group.

## 6. Authentication and networking

- Every `/api/v1/*` request requires
  `Authorization: Bearer <INTEGRATION_API_TOKEN>`.
- Tokens are at least 32 characters, secret-typed, and never committed,
  printed, logged, passed in URLs, or baked into images.
- `/health/live` and `/health/ready` are unauthenticated.
- The validated deployment binds the API to loopback.
- Remote CloudStack access still requires an approved protected network with
  TLS/mTLS, TLS termination, or another approved secure transport.
- Plain Bearer HTTP over an untrusted network is prohibited.

## 7. Validation completed

- Ruff, strict mypy, unit, parser, adapter, authentication, API,
  container-contract, fake-CLI, and complete pytest validation passed.
- Native ARM64 image build and hardened mock/CLI runtime checks passed.
- Mock-only atomic-allocation validation passed without touching the real
  eSwitch.
- The required-operation acceptance test passed with an approved temporary
  vSwitch and representor:
  `create -> observe -> attach -> observe -> detach -> observe -> delete ->
  final comparison`.
- Existing production vSwitch state remained unchanged.
- Available-port and vSwitch state returned exactly to baseline.
- The Integration API and independent daemon remained healthy.
- No VM operation, PCI attachment, driver binding, or CloudStack mutation
  occurred.

Detailed evidence omits tokens, addresses, hostnames, UUIDs, MAC addresses, VM
names, and operational logs.

## 8. Deployment and basic verification

Run on the approved BlueField checkout. Prepare the deployment `.env` through
the approved secret workflow and set mode 0600; do not put a token in shell
history or Git.

```bash
git status --short
EXPECTED_REVIEWED_COMMIT='<approved-full-commit>'
test "$(git rev-parse HEAD)" = "$EXPECTED_REVIEWED_COMMIT"
git check-ignore --quiet .env
chmod 0600 .env

docker compose config --quiet
docker compose build integration-api
docker compose up -d integration-api
docker compose ps integration-api

curl --fail --show-error http://127.0.0.1:8081/health/live
curl --fail --show-error http://127.0.0.1:8081/health/ready
```

For authenticated read-only checks, load the token through the approved secret
channel without echo, then keep it out of command-line arguments:

```bash
IFS= read -r -s -p 'Integration API token: ' INTEGRATION_API_TOKEN
printf '\n'
export INTEGRATION_API_TOKEN
python3 - <<'PY'
import os
import urllib.request

for path in ("/api/v1/vswitches", "/api/v1/ports/available"):
    request = urllib.request.Request(
        "http://127.0.0.1:8081" + path,
        headers={"Authorization": "Bearer " + os.environ["INTEGRATION_API_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        print(path, response.status)
PY
unset INTEGRATION_API_TOKEN
```

These are observations only. Do not use mutation routes as ordinary health
checks. Real mutation testing requires the
[approval-gated runbook](real-mutation-smoke-test.md).

## 9. Current status

- The MVP Integration API is complete.
- All required direct operations are implemented and validated.
- The API is deployed and running on BlueField.
- CloudStack-side client invocation is the next team integration task.
- No additional Integration API feature is required for the confirmed scope.

## 10. Known limitations and future work

Current handoff blockers:

- approved secure remote connectivity from CloudStack;
- final CloudStack-side API client and invocation;
- operational token distribution and rotation;
- replacement of supplementary group 0 with a dedicated socket group.

Optional future research, not current MVP scope:

- direct Unix-socket adapter;
- atomic allocation endpoint;
- durable allocation storage;
- distributed locking/fencing;
- router or uplink automation;
- CloudStack/KVM Agent changes;
- adoption of the VF attachment planner.

## 11. Team handoff checklist

- [ ] CloudStack team confirms authenticated endpoint access.
- [ ] Protected transport and TLS/mTLS boundary is selected.
- [ ] Token is provisioned and rotated outside Git.
- [ ] API base URL is configured in the CloudStack-owned client.
- [ ] Host/PF/VF-to-PCI mapping is implemented on the selected CloudStack host.
- [ ] HTTP, daemon-error, timeout, and rollback policy is implemented.
- [ ] Mutation requests are never blindly retried.
- [ ] Read-only membership and available-port observation follows ambiguity.
- [ ] Production vSwitch and port ownership is confirmed before mutation.

## 12. How to continue with Codex

Use this prompt template:

```text
Continue the CloudStack-BlueField Integration API project.

Before editing, read completely:
- AGENTS.md
- README.md
- docs/HANDOFF.md
- docs/integration-api-scope.md
- docs/CLI.md

Verify the current branch, HEAD, working tree, and existing diff. Preserve all
existing work.

Requested change:
<state one bounded change>

Preserve the confirmed scope: CloudStack client -> authenticated REST API ->
allowlisted eswitchctl -> existing eswitch-management daemon. Do not modify
CloudStack unless its owner explicitly requests that separate work. Do not
access or invoke real BlueField mutation endpoints without explicit approval,
approved isolated resources, and a rollback procedure.

Run ruff format --check, ruff check, strict mypy, complete pytest, focused
container-contract tests, shell syntax checks where applicable, and
git diff --check. Report exact results and stop before commit and push.
```

## 13. References

Required MVP references:

- [Integration API scope](integration-api-scope.md)
- [CLI contract](CLI.md)
- [BlueField runtime](bluefield-runtime.md)
- [Approval-gated mutation runbook](real-mutation-smoke-test.md)

Optional research references, not required MVP architecture:

- [Phase 6 allocation workflow](phase6-allocation-workflow.md)
- [VF-to-PCI resolver](vf-pci-resolver.md)
- [VF attachment planner](vf-attachment-planner.md)
- [CloudStack/KVM integration research](cloudstack-kvm-integration-design.md)
