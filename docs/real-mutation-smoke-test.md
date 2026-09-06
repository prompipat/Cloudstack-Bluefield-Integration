# Phase 7A: approval-gated real-mutation smoke test

## Status

This prospective runbook does not grant approval and records no completed
mutation. It follows [the Integration API scope](integration-api-scope.md).
The required direct endpoints are mutation-capable in CLI mode. The mock-only
atomic allocation endpoint is excluded.

The first mutation validation must keep the API bound to BlueField loopback.
Commands use `http://127.0.0.1:8081`. Do not adapt them to remote plaintext HTTP.

## Absolute safety rules

- Never mutate protected production vSwitch `100`.
- Never use port `0` or an uplink/parent port.
- Use only explicitly approved IDs; never select a real port dynamically.
- Never retry a timed-out or transport-failed mutation.
- Observe state after ambiguity and stop for reconciliation.
- Never delete a pre-existing vSwitch.
- Never detach unless this test proved it attached that exact port.
- Reverse only effects marked as test-owned in the ledger.
- Never print or commit the token.
- Never affect the independent `eswitch-management` container.

## Approval record

Store this completed template in the approved change record, not Git.

| Required field | Recorded value |
|---|---|
| Change ID | |
| Written DOCA/eSwitch owner approval and timestamp | |
| Maintenance window and timezone | |
| Approved test vSwitch ID, explicitly not 100 | |
| Approved representor port ID, explicitly not 0 | |
| Observed host/PF/VF identity | |
| KVM owner confirmation that its VF is not attached to a VM | |
| Confirmation that no process has the VFIO group open | |
| Named rollback owner | |
| Monitoring owner and escalation channel | |
| Rollback deadline | |

Every field is mandatory. The KVM/CloudStack owner supplies the VM and VFIO
evidence; this procedure does not inspect Libvirt, VMs, sysfs, or VFIO.

## Preflight checklist

- [ ] Written approval is attached and the change window is active.
- [ ] The approved vSwitch ID is explicit, in range, and not 100.
- [ ] The approved port is explicit, nonzero, and a representor.
- [ ] The port is confirmed immediately available; no replacement may be chosen.
- [ ] VM non-attachment and VFIO-open checks are current.
- [ ] Baseline status, membership, and available ports will be captured.
- [ ] API is in CLI mode, healthy, and bound only to loopback.
- [ ] Rollback and monitoring owners are present.
- [ ] Evidence storage is private and outside the Git worktree.
- [ ] Any ambiguity or unexpected state will stop the test.

## Ownership ledger

Update `owned` only after authoritative observation.

| Effect | Baseline | Requested | Observed | Owned by test | Reversed |
|---|---|---|---|---|---|
| Test vSwitch created | absent | no/yes | no/yes/unknown | no/yes | no/yes |
| Approved port attached | available | no/yes | no/yes/unknown | no/yes | no/yes |
| Approved port detached | test-owned | no/yes | no/yes/unknown | n/a | no/yes |
| Test vSwitch deleted | test-owned, empty | no/yes | no/yes/unknown | n/a | no/yes |

`unknown` stops the procedure and never proves ownership.

## Fail-closed setup

Run only on the approved BlueField after preflight. Assign IDs manually from
the signed record; do not derive either from query output.

```bash
export APPROVED_TEST_VSWITCH_ID='<approved-numeric-id>'
export APPROVED_TEST_PORT_ID='<approved-numeric-id>'
export MUTATION_APPROVAL_CONFIRMED=YES
export MAINTENANCE_WINDOW_ACTIVE=YES
export VF_NOT_ATTACHED_CONFIRMED=YES
export VFIO_GROUP_UNUSED_CONFIRMED=YES
export ROLLBACK_OWNER_PRESENT=YES

case "${APPROVED_TEST_VSWITCH_ID:-}" in
  ''|*[!0-9]*) echo 'STOP: invalid vSwitch ID' >&2; return 1 ;;
esac
case "${APPROVED_TEST_PORT_ID:-}" in
  ''|*[!0-9]*) echo 'STOP: invalid port ID' >&2; return 1 ;;
esac
[ "$APPROVED_TEST_VSWITCH_ID" -ge 1 ] &&
[ "$APPROVED_TEST_VSWITCH_ID" -le 65535 ] &&
[ "$APPROVED_TEST_VSWITCH_ID" -ne 100 ] ||
  { echo 'STOP: prohibited vSwitch ID' >&2; return 1; }
[ "$APPROVED_TEST_PORT_ID" -ge 1 ] &&
[ "$APPROVED_TEST_PORT_ID" -le 65535 ] ||
  { echo 'STOP: prohibited port ID' >&2; return 1; }
[ "$MUTATION_APPROVAL_CONFIRMED" = YES ] &&
[ "$MAINTENANCE_WINDOW_ACTIVE" = YES ] &&
[ "$VF_NOT_ATTACHED_CONFIRMED" = YES ] &&
[ "$VFIO_GROUP_UNUSED_CONFIRMED" = YES ] &&
[ "$ROLLBACK_OWNER_PRESENT" = YES ] ||
  { echo 'STOP: incomplete approval gate' >&2; return 1; }

EVIDENCE_DIR="$(mktemp -d /tmp/integration-api-phase7a.XXXXXX)"
chmod 0700 "$EVIDENCE_DIR"
export EVIDENCE_DIR
IFS= read -r -s -p 'Integration API token: ' INTEGRATION_API_TOKEN
printf '\n'
export INTEGRATION_API_TOKEN
[ "${#INTEGRATION_API_TOKEN}" -ge 32 ] ||
  { echo 'STOP: missing or short token' >&2; return 1; }
```

Do not enable shell tracing or print the environment while the token is set.

Define a local helper that keeps the token out of command arguments, writes the
body to private evidence, and prints only HTTP status:

```bash
api_request() {
  [ "$#" -eq 4 ] || return 2
  SMOKE_METHOD="$1" SMOKE_API_PATH="$2" SMOKE_JSON="$3" SMOKE_OUTPUT="$4"   python3 - <<'PY'
import os
import pathlib
import urllib.error
import urllib.request

body = os.environ["SMOKE_JSON"].encode() if os.environ["SMOKE_JSON"] else None
request = urllib.request.Request(
    "http://127.0.0.1:8081" + os.environ["SMOKE_API_PATH"],
    data=body,
    method=os.environ["SMOKE_METHOD"],
    headers={"Authorization": "Bearer " + os.environ["INTEGRATION_API_TOKEN"],
             "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        pathlib.Path(os.environ["SMOKE_OUTPUT"]).write_bytes(response.read())
        print(response.status)
except urllib.error.HTTPError as error:
    pathlib.Path(os.environ["SMOKE_OUTPUT"]).write_bytes(error.read())
    print(error.code)
PY
}
```

A helper exception, missing status, HTTP 503 during mutation, or client timeout
is ambiguous. Do not repeat the request.

## Contract and expected results

| Step | REST request | Exact `eswitchctl` mapping | Expected | Authority |
|---|---|---|---|---|
| Ready | `GET /health/ready` | `status` | 200 | daemon running |
| Membership | `GET /api/v1/vswitches` | `vs-list` | 200 | normalized current membership |
| Available | `GET /api/v1/ports/available` | `list-port-available` | 200 | approved representor present |
| Create | `POST /api/v1/vswitches`, `{"vswitch_id": ID}` | `vs-create --id ID` | 201 | `vs-list` shows empty ID |
| Attach | `POST /api/v1/vswitches/{ID}/ports`, `{"port_id": PORT}` | `vs-port-attach --id ID --port PORT` | 200 | membership shows PORT |
| Detach | `DELETE /api/v1/vswitches/{ID}/ports/{PORT}` | `vs-port-detach --id ID --port PORT` | 204 | membership absent, port available |
| Delete | `DELETE /api/v1/vswitches/{ID}` | `vs-delete --id ID` | 204 | ID absent |
| Final ready | `GET /health/ready` | `status` | 200 | daemon running |

Readiness is intentionally unauthenticated. Pair it with the authenticated
available-port request; do not describe health as protected.

## Ordered procedure

### 1. Baseline and immediate availability

```bash
curl --fail --show-error http://127.0.0.1:8081/health/ready \
  >"$EVIDENCE_DIR/before-ready.json"
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/before-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership query failed' >&2; return 1; }
STATUS="$(api_request GET /api/v1/ports/available '' \
  "$EVIDENCE_DIR/before-available.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: available-port query failed' >&2; return 1; }
```

Require readiness HTTP 200 and both authenticated queries HTTP 200. Stop if
the approved vSwitch exists. Confirm the approved port appears exactly once in
`before-available.json` as a representor with numeric `host`, `pf`, and
`vf_index`, not as an uplink. Compare H/P/V with the
signed KVM-owner evidence. Do not choose a substitute.

### 2. Health and authenticated precheck

```bash
curl --fail --show-error http://127.0.0.1:8081/health/live   >"$EVIDENCE_DIR/pre-live.json"
curl --fail --show-error http://127.0.0.1:8081/health/ready   >"$EVIDENCE_DIR/pre-ready.json"
STATUS="$(api_request GET /api/v1/ports/available ''   "$EVIDENCE_DIR/pre-available.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: precheck failed' >&2; return 1; }
```

Immediately confirm the same approved representor is present exactly once.
Disappearance or identity change stops the test.

### 3. Create and observe

Request body: `{"vswitch_id": APPROVED_TEST_VSWITCH_ID}`.
Mapping: `eswitchctl vs-create --id APPROVED_TEST_VSWITCH_ID`.
Expected: HTTP 201.

```bash
STATUS="$(api_request POST /api/v1/vswitches \
  "{\"vswitch_id\":${APPROVED_TEST_VSWITCH_ID}}" \
  "$EVIDENCE_DIR/create.json")"
[ "$STATUS" = 201 ] ||
  { echo 'STOP: observe create and reconcile; do not retry' >&2; return 1; }
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/after-create-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership observation failed' >&2; return 1; }
```

Require 201 and an empty approved vSwitch in `vs-list`. Then mark creation
owned. Rollback is deletion of this exact empty, test-created vSwitch. Stop on
any other status, timeout, query failure, unexpected member, or ambiguity.

### 4. Attach and observe

Reconfirm the vSwitch is empty and the exact approved representor remains
available. Request body: `{"port_id": APPROVED_TEST_PORT_ID}`.
Mapping: `eswitchctl vs-port-attach --id APPROVED_TEST_VSWITCH_ID --port APPROVED_TEST_PORT_ID`.
Expected: HTTP 200.

```bash
STATUS="$(api_request POST \
  "/api/v1/vswitches/${APPROVED_TEST_VSWITCH_ID}/ports" \
  "{\"port_id\":${APPROVED_TEST_PORT_ID}}" \
  "$EVIDENCE_DIR/attach.json")"
[ "$STATUS" = 200 ] ||
  { echo 'STOP: observe attach and reconcile; do not retry' >&2; return 1; }
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/after-attach-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership observation failed' >&2; return 1; }
STATUS="$(api_request GET /api/v1/ports/available '' \
  "$EVIDENCE_DIR/after-attach-available.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: observation failed' >&2; return 1; }
```

Success requires the exact membership and disappearance from available ports.
Only then mark attachment owned. Rollback may detach only that proved-owned
attachment. Stop on non-200, timeout, unexpected membership, port still
available, or monitoring alarm.

### 5. Detach and observe

Proceed only when the ledger proves this test attached the exact port.
Mapping: `eswitchctl vs-port-detach --id APPROVED_TEST_VSWITCH_ID --port APPROVED_TEST_PORT_ID`.
Expected: HTTP 204.

```bash
STATUS="$(api_request DELETE \
  "/api/v1/vswitches/${APPROVED_TEST_VSWITCH_ID}/ports/${APPROVED_TEST_PORT_ID}" \
  '' "$EVIDENCE_DIR/detach.json")"
[ "$STATUS" = 204 ] ||
  { echo 'STOP: observe detach and reconcile; do not retry' >&2; return 1; }
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/after-detach-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership observation failed' >&2; return 1; }
STATUS="$(api_request GET /api/v1/ports/available '' \
  "$EVIDENCE_DIR/after-detach-available.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: observation failed' >&2; return 1; }
```

Require the port absent from membership and present with unchanged H/P/V in
available ports. Mark reversed only after both observations. Never retry an
ambiguous detach.

### 6. Delete and observe

Proceed only if baseline proved the ID absent, the ledger proves this test
created it, and current membership proves it empty.
Mapping: `eswitchctl vs-delete --id APPROVED_TEST_VSWITCH_ID`.
Expected: HTTP 204.

```bash
STATUS="$(api_request DELETE \
  "/api/v1/vswitches/${APPROVED_TEST_VSWITCH_ID}" '' \
  "$EVIDENCE_DIR/delete.json")"
[ "$STATUS" = 204 ] ||
  { echo 'STOP: observe delete and reconcile; do not retry' >&2; return 1; }
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/after-delete-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership observation failed' >&2; return 1; }
```

Require the ID absent, then mark creation reversed. Never delete if ownership,
emptiness, response, or observation is ambiguous.

### 7. Final comparison and cleanup

```bash
curl --fail --show-error http://127.0.0.1:8081/health/ready \
  >"$EVIDENCE_DIR/after-ready.json"
STATUS="$(api_request GET /api/v1/vswitches '' \
  "$EVIDENCE_DIR/after-vs-list.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: membership observation failed' >&2; return 1; }
STATUS="$(api_request GET /api/v1/ports/available '' \
  "$EVIDENCE_DIR/after-available.json")"
[ "$STATUS" = 200 ] || { echo 'STOP: available-port observation failed' >&2; return 1; }
curl --fail --show-error http://127.0.0.1:8081/health/live   >"$EVIDENCE_DIR/post-live.json"
curl --fail --show-error http://127.0.0.1:8081/health/ready   >"$EVIDENCE_DIR/post-ready.json"
cmp -s "$EVIDENCE_DIR/before-vs-list.json" "$EVIDENCE_DIR/after-vs-list.json" ||
  { echo 'STOP: vSwitch state differs' >&2; return 1; }
cmp -s "$EVIDENCE_DIR/before-available.json" "$EVIDENCE_DIR/after-available.json" ||
  { echo 'STOP: available ports differ' >&2; return 1; }
unset INTEGRATION_API_TOKEN SMOKE_METHOD SMOKE_API_PATH SMOKE_JSON SMOKE_OUTPUT
```

Confirm final readiness remains HTTP 200. Retain private evidence until it is
redacted and signed off.

If API cleanup is approved, run `docker compose down` only from the reviewed
Integration API project after proving `docker compose config --services` lists
only `integration-api`. It must never affect `eswitch-management`.

## Ambiguous outcome

For a timeout, transport loss, missing status, HTTP 503, or unexpected state:

1. Do not repeat or compensate immediately.
2. Record sanitized timing, request path, request ID, and ledger checkpoint.
3. Query only `GET /health/ready`, authenticated `GET /api/v1/vswitches`,
   and authenticated `GET /api/v1/ports/available`. These map to `status`,
   `vs-list`, and `list-port-available`.
4. Mark observed state yes, no, or unknown; an API error is not authoritative.
5. Stop and notify DOCA/eSwitch and rollback owners.
6. Compensate only if ownership is proved and the owner explicitly authorizes
   that exact reverse operation.
7. Otherwise leave state for reconciliation.

## Rollback decisions

| Observed state | Ownership | Action |
|---|---|---|
| Test vSwitch absent | n/a | none |
| Empty test vSwitch created by test | proved | delete once, then observe |
| vSwitch creation uncertain | not proved | stop; never delete |
| Port attached by this test to its test vSwitch | proved | detach once, then observe |
| Port attachment uncertain | not proved | stop; never detach |
| Any unexpected member | irrelevant | stop; never delete |
| Port belongs elsewhere | none | stop; never detach |
| Detach/delete timed out | outcome unknown | observe and stop; never retry |
| Final baseline differs | any | preserve evidence and reconcile |

Rollback order is detach the proved-owned port, observe it available, then
delete the proved-created empty vSwitch.

## Evidence collection template

| Evidence | Timestamp | Result or private evidence reference |
|---|---|---|
| Approval and active window | | |
| VM non-attachment and VFIO-open confirmations | | |
| Baseline status, membership, and available ports | | |
| Pre/post liveness and readiness | | |
| Authenticated availability checks | | |
| Create response and observed empty vSwitch | | |
| Attach response, membership, and unavailable-port proof | | |
| Detach response, membership, and returned-port proof | | |
| Delete response and absent-vSwitch proof | | |
| Final semantic status and byte comparisons | | |
| Ownership ledger and monitoring result | | |
| Rollback or reconciliation decisions | | |
| Final owner sign-offs | | |

Capture every HTTP status/body and request ID privately. Do not commit evidence.

Redact tokens, Authorization headers, addresses, VM identifiers, MAC addresses,
VFIO/process details, daemon secrets, and full operational logs. Retain only
approved resource IDs, H/P/V identity, sanitized outcomes, timestamps, hashes,
and evidence references required by the change record.

## Final sign-off

- [ ] Every mutation has the expected status and authoritative observation.
- [ ] The port is available again with unchanged H/P/V identity.
- [ ] The test vSwitch is absent.
- [ ] Final vSwitch and available-port state match baseline.
- [ ] Status counts, health, traffic, and daemon monitoring are normal.
- [ ] No unowned port or vSwitch was detached or deleted.
- [ ] The ownership ledger shows every test effect reversed.
- [ ] Rollback and DOCA/eSwitch owners signed off.
- [ ] The token was unset and no secret entered evidence.
- [ ] Evidence was redacted and stored under the approved retention policy.

Any unchecked item leaves the change open for reconciliation.
