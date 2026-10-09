# NetDevOps Agent Rules

## Scope
This workspace is for the NetDevOps integrator project.

## Shared network devices
The devices cisco01, cisco02 and fortigate01 are shared laboratory devices and are READ ONLY.

The agent may use only read operations against these devices:
- NETCONF get / get-config
- NETCONF capability discovery
- RESTCONF GET
- FortiOS REST API GET
- connectivity checks

The agent MUST NOT use against read_only devices:
- NETCONF edit-config
- RESTCONF POST, PUT, PATCH or DELETE
- FortiOS POST, PUT or DELETE
- Netmiko configuration mode
- shutdown / no shutdown
- configuration changes of any kind

If a requirement needs a configuration change, it must be performed only on infrastructure marked access: managed.

## Preferred workflow for Cisco discovery
1. labctl.cmd check <device>
2. labctl.cmd netconf-capabilities <device>
3. Verify ietf-interfaces, ietf-ip and Cisco-IOS-XE-native capabilities.
4. labctl.cmd netconf-hostname <device>
5. labctl.cmd netconf-interfaces <device>
6. Use netconf-get with a local XML filter file when a more specific YANG query is needed.

## Preferred workflow for FortiGate discovery
1. labctl.cmd check fortigate01
2. labctl.cmd fortigate-status fortigate01
3. labctl.cmd fortigate-wan1 fortigate01
4. Use fortigate-get only for additional GET endpoints.

## Development rule
Do not invent YANG paths or FortiOS fields when the live device can be queried first. Inspect capabilities/API output, then implement parsers based on actual returned data.

## Secrets
Never write real passwords or API tokens into source files, inventory.yaml, README.md, logs or Git. Secrets must remain in ~/.lab-agent/secrets.env.
