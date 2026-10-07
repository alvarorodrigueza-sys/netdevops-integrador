# Lab Agent - NetDevOps V3 Rules

## Scope
The shared network devices in inventory.yaml are production/classroom resources provided by the instructor.
They are strictly READ ONLY.

## Shared devices
- cisco01: NETCONF/YANG read-only
- cisco02: NETCONF/YANG read-only
- fortigate01: FortiOS REST API read-only

## Allowed operations on shared devices
- Connectivity checks
- NETCONF hello/capabilities
- NETCONF get / get-config for read-only queries
- RESTCONF GET
- FortiOS REST GET
- Normalized snapshots
- Telemetry polling and bandwidth calculation

## Forbidden operations on shared devices
- NETCONF edit-config
- POST, PUT, PATCH, DELETE
- Configuration mode
- shutdown / no shutdown
- Changing interfaces, VLANs, routes, policies, users, credentials, or services

Never bypass labctl safety controls.

## Preferred data workflow
For dashboard/telemetry work, prefer normalized commands over raw XML/JSON:

1. `labctl.cmd snapshot <device>` for one normalized current-state snapshot.
2. `labctl.cmd snapshot-all` for all active interfaces/devices.
3. `labctl.cmd monitor <device> --interval 5 --samples N` for utilization calculations.
4. `labctl.cmd monitor-all --interval 5 --samples N` for all monitored devices.

Use raw commands such as `netconf-interfaces`, `netconf-get`, `fortigate-get`, or `fortigate-wan1` only for troubleshooting or model discovery.

## Bandwidth formula
Use the project formula exactly:

Utilization (%) = ((Delta RX bytes + Delta TX bytes) * 8) / (Delta time seconds * interface speed bps) * 100

Do not invent missing interface speeds. If speed is zero/unknown, utilization must remain null until a valid speed is available.

## 70 percent threshold
The shared read-only devices may be monitored and may raise `threshold_exceeded=true`, but the agent MUST NOT attempt mitigation on them.
Automatic mitigation will only be enabled later for devices explicitly marked `access: managed`.

## Dashboard goal
The final dashboard should consume normalized data, not vendor-specific raw payloads.
Use the common fields:
- device
- hostname
- vendor
- interface
- oper_status
- speed_bps
- rx_bytes
- tx_bytes
- traffic_bps
- utilization_pct
- threshold_exceeded
- rx_errors / tx_errors when available
