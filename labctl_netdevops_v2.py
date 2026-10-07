import argparse
import asyncio
import json
import os
import re
import shlex
import socket
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import paramiko
import paho.mqtt.client as mqtt
import requests
import serial
import yaml

from dotenv import load_dotenv
from netmiko import ConnectHandler


BASE_DIR = Path(__file__).resolve().parent
INVENTORY_FILE = BASE_DIR / "inventory.yaml"
LOG_DIR = BASE_DIR / "logs"

SECRETS_FILE = Path.home() / ".lab-agent" / "secrets.env"

LOG_DIR.mkdir(exist_ok=True)

if SECRETS_FILE.exists():
    load_dotenv(SECRETS_FILE)


BLOCKED_PATTERNS = [
    r"\bwrite\s+erase\b",
    r"\berase\s+startup-config\b",
    r"\bfactory[- ]?reset\b",
    r"\bexecute\s+factoryreset\b",
    r"\bformat\s+flash",
    r"\bdelete\s+flash:",
    r"\bmkfs\b",
    r"\bwipefs\b",
    r"\brm\s+-rf\s+/\b",
]


def die(message, code=1):
    print(f"[FAIL] {message}")
    raise SystemExit(code)


def ok(message):
    print(f"[OK] {message}")


def guard(commands):
    if isinstance(commands, str):
        commands = [commands]

    for command in commands:
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                die(
                    f"Destructive command blocked: {command}",
                    90,
                )


def load_inventory():
    if not INVENTORY_FILE.exists():
        die(f"Inventory not found: {INVENTORY_FILE}")

    with INVENTORY_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data.get("devices", {})


def get_device(name):
    devices = load_inventory()

    if name not in devices:
        die(f"Unknown device: {name}")

    device = devices[name]

    if not device.get("enabled", True):
        die(f"Device '{name}' is disabled in inventory.yaml")

    return device


def access_mode(device):
    return str(device.get("access", "managed")).lower()


def require_managed(device, operation):
    if access_mode(device) == "read_only":
        die(
            f"Operation '{operation}' blocked: device is read_only",
            91,
        )


def pretty_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False))


def get_secret(device, field):
    variable = device.get(field)

    if not variable:
        return None

    value = os.getenv(variable)

    if value is None:
        die(f"Missing secret environment variable: {variable}")

    return value


def show_inventory():
    devices = load_inventory()

    print(
        f"{'NAME':<20}"
        f"{'STATE':<10}"
        f"{'DRIVER':<12}"
        f"{'TARGET'}"
    )
    print("-" * 70)

    for name, device in devices.items():
        state = "enabled" if device.get("enabled", True) else "disabled"
        driver = device.get("driver", "?")
        target = (
            device.get("host")
            or device.get("base_url")
            or device.get("port")
            or "?"
        )

        print(
            f"{name:<20}"
            f"{state:<10}"
            f"{driver:<12}"
            f"{target}"
        )


# ---------------------------------------------------------
# Generic SSH / Paramiko
# ---------------------------------------------------------

def paramiko_connect(device):
    client = paramiko.SSHClient()

    known_hosts = Path.home() / ".ssh" / "known_hosts"

    if known_hosts.exists():
        try:
            client.load_host_keys(str(known_hosts))
        except Exception:
            pass

    # Practical for changing classroom/lab VMs.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    password = get_secret(device, "password_env")
    key_file = device.get("key_file")

    if key_file:
        key_file = os.path.expandvars(
            os.path.expanduser(key_file)
        )

    client.connect(
        hostname=device["host"],
        port=int(device.get("port", 22)),
        username=device.get("username"),
        password=password,
        key_filename=key_file,
        timeout=10,
        banner_timeout=15,
        auth_timeout=15,
    )

    return client


def ssh_exec(device, command):
    guard(command)

    print(f"[COMMAND] {command}")

    client = paramiko_connect(device)

    try:
        stdin, stdout, stderr = client.exec_command(
            command,
            timeout=300,
        )

        out = stdout.read().decode(
            "utf-8",
            errors="replace",
        )

        err = stderr.read().decode(
            "utf-8",
            errors="replace",
        )

        exit_code = stdout.channel.recv_exit_status()

        if out:
            print("\n----- STDOUT -----")
            print(out.rstrip())

        if err:
            print("\n----- STDERR -----")
            print(err.rstrip())

        print(f"\n[EXIT_CODE] {exit_code}")

        raise SystemExit(exit_code)

    finally:
        client.close()


def ssh_sudo_exec(device, command):
    guard(command)

    sudo_password = get_secret(
        device,
        "sudo_password_env",
    )

    if not sudo_password:
        sudo_password = get_secret(
            device,
            "password_env",
        )

    if not sudo_password:
        die("No sudo password configured for this device")

    print(f"[SUDO COMMAND] {command}")

    client = paramiko_connect(device)

    try:
        quoted_command = shlex.quote(command)

        remote_command = (
            "sudo -S -p '' "
            "bash -lc "
            + quoted_command
        )

        stdin, stdout, stderr = client.exec_command(
            remote_command,
            timeout=300,
        )

        stdin.write(sudo_password + "\n")
        stdin.flush()

        out = stdout.read().decode(
            "utf-8",
            errors="replace",
        )

        err = stderr.read().decode(
            "utf-8",
            errors="replace",
        )

        exit_code = stdout.channel.recv_exit_status()

        if out:
            print("\n----- STDOUT -----")
            print(out.rstrip())

        if err:
            print("\n----- STDERR -----")
            print(err.rstrip())

        print(f"\n[EXIT_CODE] {exit_code}")

        raise SystemExit(exit_code)

    finally:
        client.close()


def sftp_upload(device, local_file, remote_file):
    client = paramiko_connect(device)

    try:
        sftp = client.open_sftp()

        sftp.put(
            str(Path(local_file).resolve()),
            remote_file,
        )

        sftp.close()

        ok(f"Uploaded {local_file} -> {remote_file}")

    finally:
        client.close()


def sftp_download(device, remote_file, local_file):
    client = paramiko_connect(device)

    try:
        sftp = client.open_sftp()

        sftp.get(
            remote_file,
            str(Path(local_file).resolve()),
        )

        sftp.close()

        ok(f"Downloaded {remote_file} -> {local_file}")

    finally:
        client.close()


# ---------------------------------------------------------
# Netmiko
# ---------------------------------------------------------

def netmiko_connect(name, device):
    password = get_secret(
        device,
        "password_env",
    ) or ""

    secret = get_secret(
        device,
        "secret_env",
    ) or ""

    params = {
        "device_type": device["device_type"],
        "host": device["host"],
        "port": int(device.get("port", 22)),
        "username": device.get("username", ""),
        "password": password,
        "secret": secret,
        "conn_timeout": 10,
        "banner_timeout": 15,
        "auth_timeout": 15,
        "session_log": str(
            LOG_DIR / f"{name}_session.log"
        ),
    }

    return ConnectHandler(**params)


def netmiko_exec(name, device, command):
    guard(command)

    print(f"[DEVICE] {name}")
    print(f"[COMMAND] {command}\n")

    connection = netmiko_connect(name, device)

    try:
        if device.get("use_enable", False):
            connection.enable()

        output = connection.send_command(
            command,
            read_timeout=60,
        )

        print(output)

        ok("Command completed")

    finally:
        connection.disconnect()


def netmiko_config(name, device, commands):
    guard(commands)

    print(f"[DEVICE] {name}")
    print("[CONFIG]")

    for command in commands:
        print(f"  {command}")

    connection = netmiko_connect(name, device)

    try:
        if device.get("use_enable", False):
            connection.enable()

        output = connection.send_config_set(
            commands,
            read_timeout=90,
        )

        print("\n----- DEVICE OUTPUT -----")
        print(output)

        ok("Configuration applied")

    finally:
        connection.disconnect()


def netmiko_config_file(name, device, filename):
    path = Path(filename)

    if not path.exists():
        die(f"Configuration file not found: {filename}")

    commands = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            command = line.strip()

            if not command:
                continue

            if command.startswith("#"):
                continue

            commands.append(command)

    netmiko_config(
        name,
        device,
        commands,
    )


# ---------------------------------------------------------
# HTTP / REST
# ---------------------------------------------------------

def http_request(device, method, path, json_body=None):
    base_url = device["base_url"].rstrip("/")
    url = base_url + "/" + path.lstrip("/")

    headers = dict(
        device.get("headers", {})
    )

    token = get_secret(
        device,
        "token_env",
    )

    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None

    if json_body:
        body = json.loads(json_body)

    response = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        json=body,
        timeout=20,
        verify=device.get("verify_tls", True),
    )

    print(f"[URL] {url}")
    print(f"[HTTP STATUS] {response.status_code}")

    if response.text:
        print("\n----- RESPONSE -----")
        print(response.text)

    if not 200 <= response.status_code < 400:
        raise SystemExit(2)


# ---------------------------------------------------------
# MQTT
# ---------------------------------------------------------

def build_mqtt_client(device):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    username = get_secret(
        device,
        "username_env",
    )

    password = get_secret(
        device,
        "password_env",
    )

    if username:
        client.username_pw_set(
            username,
            password,
        )

    return client


def mqtt_publish(device, topic, payload):
    client = build_mqtt_client(device)

    client.connect(
        device["host"],
        int(device.get("port", 1883)),
        30,
    )

    client.loop_start()

    result = client.publish(
        topic,
        payload,
    )

    result.wait_for_publish()

    client.loop_stop()
    client.disconnect()

    ok(f"Published {topic} = {payload}")


def mqtt_subscribe(device, topic, timeout):
    received = threading.Event()

    client = build_mqtt_client(device)

    def on_message(client, userdata, message):
        payload = message.payload.decode(
            "utf-8",
            errors="replace",
        )

        print(
            f"[MQTT] {message.topic} = {payload}"
        )

        received.set()

    client.on_message = on_message

    client.connect(
        device["host"],
        int(device.get("port", 1883)),
        30,
    )

    client.subscribe(topic)
    client.loop_start()

    success = received.wait(timeout)

    client.loop_stop()
    client.disconnect()

    if not success:
        die(
            "No MQTT message received before timeout",
            2,
        )


# ---------------------------------------------------------
# Serial console
# ---------------------------------------------------------

def serial_exec(device, command, wait):
    guard(command)

    port = device["port"]
    baudrate = int(
        device.get("baudrate", 9600)
    )

    print(f"[SERIAL] {port} @ {baudrate}")
    print(f"[COMMAND] {command}")

    with serial.Serial(
        port=port,
        baudrate=baudrate,
        timeout=0.5,
    ) as ser:

        ser.reset_input_buffer()

        ser.write(b"\r\n")
        time.sleep(0.4)

        ser.write(
            (command + "\r\n").encode()
        )

        time.sleep(wait)

        output = ser.read_all().decode(
            "utf-8",
            errors="replace",
        )

        print(output)


# ---------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------

def tcp_check(device):
    driver = device.get("driver")

    if driver == "serial":
        port = device["port"]

        try:
            with serial.Serial(
                port=port,
                baudrate=int(
                    device.get("baudrate", 9600)
                ),
                timeout=1,
            ):
                pass

            ok(f"Serial port {port} accessible")
            return

        except Exception as exc:
            die(f"Serial port failed: {exc}")

    if driver == "http":
        base_url = device["base_url"]

        try:
            response = requests.get(
                base_url,
                timeout=10,
                verify=device.get("verify_tls", True),
            )

            print(
                f"[HTTP STATUS] {response.status_code}"
            )

            ok(f"HTTP target reachable: {base_url}")
            return

        except Exception as exc:
            die(f"HTTP check failed: {exc}")

    host = device.get("host")

    if not host:
        die("Device does not define a host")

    default_ports = {
        "paramiko": 22,
        "netmiko": 22,
        "mqtt": 1883,
        "snmp": 161,
        "netconf": 830,
    }

    port = int(
        device.get(
            "port",
            default_ports.get(driver, 0),
        )
    )

    if not port:
        die("Unable to determine target port")

    print(f"[CHECK] {host}:{port}")

    with socket.create_connection(
        (host, port),
        timeout=5,
    ):
        pass

    ok(f"{host}:{port} reachable")


# ---------------------------------------------------------
# SNMP
# ---------------------------------------------------------

async def snmp_get_async(device, oid):
    # Lazy import so SNMP changes never break the rest of labctl.
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
    )

    community = get_secret(
        device,
        "community_env",
    )

    if not community:
        community = device.get(
            "community",
            "public",
        )

    engine = SnmpEngine()

    target = await UdpTransportTarget.create(
        (
            device["host"],
            int(device.get("port", 161)),
        ),
        timeout=2,
        retries=1,
    )

    (
        error_indication,
        error_status,
        error_index,
        var_binds,
    ) = await get_cmd(
        engine,
        CommunityData(community),
        target,
        ContextData(),
        ObjectType(
            ObjectIdentity(oid)
        ),
    )

    try:
        if error_indication:
            die(str(error_indication), 2)

        if error_status:
            die(
                error_status.prettyPrint(),
                2,
            )

        for var_bind in var_binds:
            print(
                " = ".join(
                    item.prettyPrint()
                    for item in var_bind
                )
            )

    finally:
        engine.close_dispatcher()


def snmp_get(device, oid):
    asyncio.run(
        snmp_get_async(device, oid)
    )


# ---------------------------------------------------------
# NETCONF
# ---------------------------------------------------------

def netconf_running(device):
    from ncclient import manager

    password = get_secret(
        device,
        "password_env",
    )

    parameters = {
        "host": device["host"],
        "port": int(device.get("port", 830)),
        "username": device.get("username"),
        "password": password,
        "hostkey_verify": False,
        "allow_agent": False,
        "look_for_keys": False,
        "timeout": 20,
    }

    device_type = device.get(
        "device_type"
    )

    if device_type:
        parameters["device_params"] = {
            "name": device_type
        }

    with manager.connect(**parameters) as session:
        result = session.get_config(
            source="running"
        )

        print(result.data_xml)


# ---------------------------------------------------------
# NetDevOps read-only helpers
# ---------------------------------------------------------

def netconf_connect(device):
    from ncclient import manager

    password = get_secret(device, "password_env")

    parameters = {
        "host": device["host"],
        "port": int(device.get("port", 830)),
        "username": device.get("username"),
        "password": password,
        "hostkey_verify": False,
        "allow_agent": False,
        "look_for_keys": False,
        "timeout": int(device.get("timeout", 20)),
    }

    device_type = device.get("device_type")
    if device_type:
        parameters["device_params"] = {"name": device_type}

    return manager.connect(**parameters)


def netconf_capabilities(device, yang_filter=None):
    with netconf_connect(device) as session:
        capabilities = sorted(str(c) for c in session.server_capabilities)

    if yang_filter:
        needle = yang_filter.lower()
        capabilities = [c for c in capabilities if needle in c.lower()]

    print(f"[CAPABILITIES] {len(capabilities)}")
    for capability in capabilities:
        print(capability)


def netconf_get(device, filter_xml=None):
    with netconf_connect(device) as session:
        if filter_xml:
            reply = session.get(filter=("subtree", filter_xml))
        else:
            reply = session.get()
    print(reply.data_xml)


def netconf_get_filter_file(device, filter_file):
    path = Path(filter_file)
    if not path.exists():
        die(f"NETCONF filter file not found: {filter_file}")
    filter_xml = path.read_text(encoding="utf-8").strip()
    netconf_get(device, filter_xml)


def netconf_interfaces(device):
    # Standards-based IETF interface operational data.
    filter_xml = """
<interfaces-state xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">
  <interface/>
</interfaces-state>
""".strip()
    netconf_get(device, filter_xml)


def netconf_native_hostname(device):
    # Cisco IOS-XE native YANG: read hostname only.
    filter_xml = """
<native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
  <hostname/>
</native>
""".strip()
    netconf_get(device, filter_xml)


def requests_auth(device):
    username = device.get("username")
    password = get_secret(device, "password_env")
    if username and password:
        return (username, password)
    return None


def read_only_http_request(device, path, *, headers=None, params=None, use_basic_auth=False):
    base_url = device["base_url"].rstrip("/")
    url = base_url + "/" + path.lstrip("/")
    request_headers = dict(device.get("headers", {}))
    if headers:
        request_headers.update(headers)

    token = get_secret(device, "token_env")
    token_style = str(device.get("token_style", "bearer")).lower()
    query = dict(params or {})
    if token:
        if token_style == "query":
            query[device.get("token_param", "access_token")] = token
        else:
            request_headers["Authorization"] = f"Bearer {token}"

    verify_tls = bool(device.get("verify_tls", True))
    response = requests.get(
        url,
        headers=request_headers,
        params=query,
        auth=requests_auth(device) if use_basic_auth else None,
        timeout=int(device.get("timeout", 20)),
        verify=verify_tls,
    )

    print(f"[URL] {response.url}")
    print(f"[HTTP STATUS] {response.status_code}")

    if not 200 <= response.status_code < 400:
        if response.text:
            print(response.text)
        raise SystemExit(2)

    try:
        pretty_json(response.json())
    except ValueError:
        print(response.text)


def restconf_get(device, path):
    headers = {
        "Accept": "application/yang-data+json",
        "Content-Type": "application/yang-data+json",
    }
    read_only_http_request(
        device,
        path,
        headers=headers,
        use_basic_auth=True,
    )


def fortigate_get(device, path):
    read_only_http_request(
        device,
        path,
        headers={"Accept": "application/json"},
        use_basic_auth=False,
    )


def fortigate_status(device):
    fortigate_get(device, "/api/v2/monitor/system/status")


def fortigate_wan1(device):
    # FortiOS monitor endpoint supports interface_name filtering on common releases.
    read_only_http_request(
        device,
        "/api/v2/monitor/system/interface",
        headers={"Accept": "application/json"},
        params={"interface_name": device.get("monitor_interface", "wan1")},
        use_basic_auth=False,
    )


def netdevops_probe(name, device):
    driver = device.get("driver")
    print(f"[DEVICE] {name}")
    print(f"[ACCESS] {access_mode(device)}")

    if driver == "netconf":
        with netconf_connect(device) as session:
            caps = [str(c) for c in session.server_capabilities]
        wanted = [
            "ietf-interfaces",
            "ietf-ip",
            "Cisco-IOS-XE-native",
        ]
        found = {w: any(w.lower() in c.lower() for c in caps) for w in wanted}
        pretty_json({
            "device": name,
            "host": device.get("host"),
            "netconf": "ok",
            "required_models": found,
            "capability_count": len(caps),
        })
        return

    if driver == "http" or device.get("base_url"):
        path = device.get("probe_path", "/api/v2/monitor/system/status")
        fortigate_get(device, path)
        return

    die(f"No NetDevOps probe available for driver '{driver}'")


# ---------------------------------------------------------
# Generic dispatcher
# ---------------------------------------------------------

def generic_exec(name, device, command):
    driver = device.get("driver")

    if driver == "paramiko":
        ssh_exec(device, command)

    elif driver == "netmiko":
        netmiko_exec(
            name,
            device,
            command,
        )

    elif driver == "serial":
        serial_exec(
            device,
            command,
            1.5,
        )

    else:
        die(
            f"'exec' is not supported for driver '{driver}'"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Lab Agent NetDevOps controller (read-only network API support)"
    )

    commands = parser.add_subparsers(
        dest="action",
        required=True,
    )

    commands.add_parser("inventory")

    command = commands.add_parser("check")
    command.add_argument("device")

    command = commands.add_parser("exec")
    command.add_argument("device")
    command.add_argument("command")

    command = commands.add_parser("sudo-exec")
    command.add_argument("device")
    command.add_argument("command")

    command = commands.add_parser("config")
    command.add_argument("device")
    command.add_argument("commands", nargs="+")

    command = commands.add_parser("config-file")
    command.add_argument("device")
    command.add_argument("file")

    command = commands.add_parser("upload")
    command.add_argument("device")
    command.add_argument("local_file")
    command.add_argument("remote_file")

    command = commands.add_parser("download")
    command.add_argument("device")
    command.add_argument("remote_file")
    command.add_argument("local_file")

    command = commands.add_parser("http")
    command.add_argument("device")
    command.add_argument("method")
    command.add_argument("path")
    command.add_argument("--json")

    command = commands.add_parser("mqtt-pub")
    command.add_argument("device")
    command.add_argument("topic")
    command.add_argument("payload")

    command = commands.add_parser("mqtt-sub")
    command.add_argument("device")
    command.add_argument("topic")
    command.add_argument(
        "--timeout",
        type=float,
        default=10,
    )

    command = commands.add_parser("serial-exec")
    command.add_argument("device")
    command.add_argument("command")
    command.add_argument(
        "--wait",
        type=float,
        default=1.5,
    )

    command = commands.add_parser("snmp-get")
    command.add_argument("device")
    command.add_argument("oid")

    command = commands.add_parser("netconf-running")
    command.add_argument("device")

    command = commands.add_parser("netconf-capabilities")
    command.add_argument("device")
    command.add_argument("--filter", dest="yang_filter")

    command = commands.add_parser("netconf-get")
    command.add_argument("device")
    command.add_argument("--filter-file")

    command = commands.add_parser("netconf-interfaces")
    command.add_argument("device")

    command = commands.add_parser("netconf-hostname")
    command.add_argument("device")

    command = commands.add_parser("restconf-get")
    command.add_argument("device")
    command.add_argument("path")

    command = commands.add_parser("fortigate-get")
    command.add_argument("device")
    command.add_argument("path")

    command = commands.add_parser("fortigate-status")
    command.add_argument("device")

    command = commands.add_parser("fortigate-wan1")
    command.add_argument("device")

    command = commands.add_parser("netdevops-probe")
    command.add_argument("device")

    args = parser.parse_args()

    try:
        if args.action == "inventory":
            show_inventory()
            return

        device = get_device(args.device)

        if args.action == "check":
            tcp_check(device)

        elif args.action == "exec":
            generic_exec(
                args.device,
                device,
                args.command,
            )

        elif args.action == "sudo-exec":
            require_managed(device, "sudo-exec")
            if device.get("driver") != "paramiko":
                die(
                    "'sudo-exec' requires a Paramiko device"
                )

            ssh_sudo_exec(
                device,
                args.command,
            )

        elif args.action == "config":
            require_managed(device, "config")
            if device.get("driver") != "netmiko":
                die(
                    "'config' currently requires "
                    "a Netmiko device"
                )

            netmiko_config(
                args.device,
                device,
                args.commands,
            )

        elif args.action == "config-file":
            require_managed(device, "config-file")
            if device.get("driver") != "netmiko":
                die(
                    "'config-file' currently requires "
                    "a Netmiko device"
                )

            netmiko_config_file(
                args.device,
                device,
                args.file,
            )

        elif args.action == "upload":
            require_managed(device, "upload")
            sftp_upload(
                device,
                args.local_file,
                args.remote_file,
            )

        elif args.action == "download":
            sftp_download(
                device,
                args.remote_file,
                args.local_file,
            )

        elif args.action == "http":
            if access_mode(device) == "read_only" and args.method.upper() != "GET":
                die("Only HTTP GET is allowed on read_only devices", 91)
            http_request(
                device,
                args.method,
                args.path,
                args.json,
            )

        elif args.action == "mqtt-pub":
            mqtt_publish(
                device,
                args.topic,
                args.payload,
            )

        elif args.action == "mqtt-sub":
            mqtt_subscribe(
                device,
                args.topic,
                args.timeout,
            )

        elif args.action == "serial-exec":
            serial_exec(
                device,
                args.command,
                args.wait,
            )

        elif args.action == "snmp-get":
            snmp_get(
                device,
                args.oid,
            )

        elif args.action == "netconf-running":
            netconf_running(device)

        elif args.action == "netconf-capabilities":
            netconf_capabilities(device, args.yang_filter)

        elif args.action == "netconf-get":
            if args.filter_file:
                netconf_get_filter_file(device, args.filter_file)
            else:
                netconf_get(device)

        elif args.action == "netconf-interfaces":
            netconf_interfaces(device)

        elif args.action == "netconf-hostname":
            netconf_native_hostname(device)

        elif args.action == "restconf-get":
            restconf_get(device, args.path)

        elif args.action == "fortigate-get":
            fortigate_get(device, args.path)

        elif args.action == "fortigate-status":
            fortigate_status(device)

        elif args.action == "fortigate-wan1":
            fortigate_wan1(device)

        elif args.action == "netdevops-probe":
            netdevops_probe(args.device, device)

    except KeyboardInterrupt:
        die("Interrupted by user", 130)

    except SystemExit:
        raise

    except Exception as exc:
        print(
            f"[FAIL] {type(exc).__name__}: {exc}"
        )

        raise SystemExit(1)


if __name__ == "__main__":
    main()
