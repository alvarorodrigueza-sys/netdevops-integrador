import argparse
import asyncio
import json
import os
import re
import shlex
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
    return str(device.get("access", "managed")).strip().lower()


def require_write_access(name, device, action):
    if access_mode(device) == "read_only":
        die(
            f"Device '{name}' is read_only; action '{action}' is blocked",
            91,
        )


def audit(name, action, status, target=None, detail=None):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device": name,
        "action": action,
        "status": status,
    }
    if target is not None:
        record["target"] = target
    if detail is not None:
        record["detail"] = detail

    path = LOG_DIR / "audit.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
# Managed Linux file/service operations
# ---------------------------------------------------------

def ssh_capture(device, command, sudo=False, timeout=300):
    guard(command)
    client = paramiko_connect(device)

    try:
        remote_command = command
        sudo_password = None

        if sudo:
            sudo_password = get_secret(device, "sudo_password_env")
            if not sudo_password:
                sudo_password = get_secret(device, "password_env")
            if not sudo_password:
                die("No sudo password configured for this device")

            remote_command = "sudo -S -p '' bash -lc " + shlex.quote(command)

        stdin, stdout, stderr = client.exec_command(
            remote_command,
            timeout=timeout,
        )

        if sudo:
            stdin.write(sudo_password + "\n")
            stdin.flush()

        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    finally:
        client.close()


def ensure_paramiko(name, device, action):
    if device.get("driver") != "paramiko":
        die(f"'{action}' requires a Paramiko Linux device")


def remote_file_exists(device, remote_file):
    code, _, _ = ssh_capture(
        device,
        f"test -e {shlex.quote(remote_file)}",
        sudo=True,
    )
    return code == 0


def remote_file_metadata(device, remote_file):
    code, out, err = ssh_capture(
        device,
        "stat -c '%U|%G|%a' -- " + shlex.quote(remote_file),
        sudo=True,
    )
    if code != 0:
        return None

    parts = out.strip().split("|")
    if len(parts) != 3:
        return None

    return {
        "owner": parts[0],
        "group": parts[1],
        "mode": parts[2],
    }


def read_remote_file(name, device, remote_file):
    ensure_paramiko(name, device, "read-file")
    code, out, err = ssh_capture(
        device,
        "cat -- " + shlex.quote(remote_file),
        sudo=True,
    )

    if code != 0:
        if err:
            print(err.rstrip())
        die(f"Unable to read remote file: {remote_file}", code or 2)

    print(out, end="" if out.endswith("\n") else "\n")
    audit(name, "read-file", "ok", remote_file)


def upload_temp_file(device, local_file):
    local_path = Path(local_file)
    if not local_path.exists():
        die(f"Local file not found: {local_file}")

    remote_tmp = f"/tmp/lab-agent-{uuid4().hex}"
    client = paramiko_connect(device)
    try:
        sftp = client.open_sftp()
        sftp.put(str(local_path.resolve()), remote_tmp)
        sftp.close()
    finally:
        client.close()

    return remote_tmp


def cleanup_remote_temp(device, remote_tmp):
    try:
        ssh_capture(device, "rm -f -- " + shlex.quote(remote_tmp), sudo=False)
    except Exception:
        pass


def diff_remote_file(name, device, local_file, remote_file):
    ensure_paramiko(name, device, "diff-file")
    remote_tmp = upload_temp_file(device, local_file)

    try:
        if not remote_file_exists(device, remote_file):
            print(f"[INFO] Remote file does not exist: {remote_file}")
            print(f"[INFO] Local candidate will create a new file: {local_file}")
            audit(name, "diff-file", "new-file", remote_file)
            return

        command = (
            "diff -u --label " + shlex.quote("REMOTE:" + remote_file)
            + " --label " + shlex.quote("LOCAL:" + str(local_file))
            + " -- " + shlex.quote(remote_file)
            + " " + shlex.quote(remote_tmp)
        )
        code, out, err = ssh_capture(device, command, sudo=True)

        if out:
            print(out.rstrip())
        if err:
            print(err.rstrip())

        if code == 0:
            ok("No differences")
            audit(name, "diff-file", "identical", remote_file)
        elif code == 1:
            audit(name, "diff-file", "different", remote_file)
        else:
            die("diff failed", code)

    finally:
        cleanup_remote_temp(device, remote_tmp)


def backup_remote_file(name, device, remote_file):
    ensure_paramiko(name, device, "backup-file")
    require_write_access(name, device, "backup-file")

    if not remote_file_exists(device, remote_file):
        print(f"[INFO] Nothing to back up; file does not exist: {remote_file}")
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(remote_file).name
    backup_dir = f"/var/backups/lab-agent/{name}"
    backup_path = f"{backup_dir}/{base}.{stamp}.bak"

    command = (
        f"mkdir -p {shlex.quote(backup_dir)} && "
        f"cp -a -- {shlex.quote(remote_file)} {shlex.quote(backup_path)}"
    )
    code, out, err = ssh_capture(device, command, sudo=True)

    if code != 0:
        if err:
            print(err.rstrip())
        die("Backup failed", code)

    ok(f"Backup created: {backup_path}")
    audit(name, "backup-file", "ok", remote_file, backup_path)
    return backup_path


def restore_backup(name, device, backup_path, remote_file):
    if backup_path:
        command = (
            f"cp -a -- {shlex.quote(backup_path)} {shlex.quote(remote_file)}"
        )
    else:
        command = f"rm -f -- {shlex.quote(remote_file)}"

    code, _, err = ssh_capture(device, command, sudo=True)
    if code != 0:
        if err:
            print(err.rstrip())
        die("Automatic rollback failed", code)

    audit(name, "rollback", "ok", remote_file, backup_path or "removed-new-file")
    ok("Rollback completed")


def validate_remote(name, device, command):
    ensure_paramiko(name, device, "validate")
    print(f"[VALIDATE] {command}")
    code, out, err = ssh_capture(device, command, sudo=True)

    if out:
        print(out.rstrip())
    if err:
        print(err.rstrip())

    status = "ok" if code == 0 else "failed"
    audit(name, "validate", status, detail=command)

    if code != 0:
        die("Validation failed", code)

    ok("Validation passed")


def service_action(name, device, service, action):
    ensure_paramiko(name, device, "service")

    modifying = action not in {"status", "is-active", "is-enabled"}
    if modifying:
        require_write_access(name, device, f"service:{action}")

    service_q = shlex.quote(service)

    if action == "status":
        command = f"systemctl --no-pager --full status {service_q}"
    elif action == "is-active":
        command = f"systemctl is-active {service_q}"
    elif action == "is-enabled":
        command = f"systemctl is-enabled {service_q}"
    else:
        command = f"systemctl {action} {service_q}"

    print(f"[SERVICE] {service} -> {action}")
    code, out, err = ssh_capture(device, command, sudo=True)

    if out:
        print(out.rstrip())
    if err:
        print(err.rstrip())

    status = "ok" if code == 0 else "failed"
    audit(name, "service", status, service, action)

    if code != 0:
        die(f"Service action failed: {service} {action}", code)

    ok(f"Service action completed: {service} {action}")


def deploy_remote_file(
    name,
    device,
    local_file,
    remote_file,
    owner=None,
    group=None,
    mode=None,
    validate_command=None,
    service=None,
    service_action_name="restart",
):
    ensure_paramiko(name, device, "deploy-file")
    require_write_access(name, device, "deploy-file")

    local_path = Path(local_file)
    if not local_path.exists():
        die(f"Local file not found: {local_file}")

    existing = remote_file_exists(device, remote_file)
    metadata = remote_file_metadata(device, remote_file) if existing else None

    owner = owner or (metadata or {}).get("owner") or "root"
    group = group or (metadata or {}).get("group") or "root"
    mode = str(mode or (metadata or {}).get("mode") or "0644")

    print(f"[DEPLOY] {local_file} -> {remote_file}")
    print(f"[META] owner={owner} group={group} mode={mode}")

    remote_tmp = upload_temp_file(device, local_file)
    backup_path = None

    try:
        if existing:
            backup_path = backup_remote_file(name, device, remote_file)

        install_command = (
            "install -D "
            f"-o {shlex.quote(owner)} "
            f"-g {shlex.quote(group)} "
            f"-m {shlex.quote(mode)} "
            f"-- {shlex.quote(remote_tmp)} {shlex.quote(remote_file)}"
        )
        code, out, err = ssh_capture(device, install_command, sudo=True)

        if code != 0:
            if err:
                print(err.rstrip())
            audit(name, "deploy-file", "install-failed", remote_file)
            die("File installation failed", code)

        if validate_command:
            print(f"[VALIDATE] {validate_command}")
            code, out, err = ssh_capture(
                device,
                validate_command,
                sudo=True,
            )

            if out:
                print(out.rstrip())
            if err:
                print(err.rstrip())

            if code != 0:
                print("[FAIL] Validation failed; restoring previous file")
                restore_backup(name, device, backup_path, remote_file)
                audit(name, "deploy-file", "rolled-back-validation", remote_file)
                raise SystemExit(code or 3)

            ok("Validation passed")

        if service:
            print(f"[SERVICE] {service} -> {service_action_name}")
            cmd = f"systemctl {service_action_name} {shlex.quote(service)}"
            code, out, err = ssh_capture(device, cmd, sudo=True)

            if out:
                print(out.rstrip())
            if err:
                print(err.rstrip())

            if code == 0:
                code, active_out, active_err = ssh_capture(
                    device,
                    f"systemctl is-active {shlex.quote(service)}",
                    sudo=True,
                )
                if active_out:
                    print(active_out.rstrip())
                if active_err:
                    print(active_err.rstrip())

            if code != 0:
                print("[FAIL] Service verification failed; restoring previous file")
                restore_backup(name, device, backup_path, remote_file)
                ssh_capture(
                    device,
                    f"systemctl restart {shlex.quote(service)}",
                    sudo=True,
                )
                audit(name, "deploy-file", "rolled-back-service", remote_file, service)
                raise SystemExit(code or 4)

            ok(f"Service healthy: {service}")

        audit(name, "deploy-file", "ok", remote_file, backup_path)
        ok(f"Deployment completed: {remote_file}")
        if backup_path:
            print(f"[BACKUP] {backup_path}")

    finally:
        cleanup_remote_temp(device, remote_tmp)


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
        description="General-purpose laboratory device controller"
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

    command = commands.add_parser("read-file")
    command.add_argument("device")
    command.add_argument("remote_file")

    command = commands.add_parser("diff-file")
    command.add_argument("device")
    command.add_argument("local_file")
    command.add_argument("remote_file")

    command = commands.add_parser("backup-file")
    command.add_argument("device")
    command.add_argument("remote_file")

    command = commands.add_parser("validate")
    command.add_argument("device")
    command.add_argument("command")

    command = commands.add_parser("service")
    command.add_argument("device")
    command.add_argument("service")
    command.add_argument(
        "action",
        choices=[
            "status", "is-active", "is-enabled",
            "start", "stop", "restart", "reload",
            "enable", "disable",
        ],
    )

    command = commands.add_parser("deploy-file")
    command.add_argument("device")
    command.add_argument("local_file")
    command.add_argument("remote_file")
    command.add_argument("--owner")
    command.add_argument("--group")
    command.add_argument("--mode")
    command.add_argument("--validate")
    command.add_argument("--service")
    command.add_argument(
        "--service-action",
        choices=["restart", "reload"],
        default="restart",
    )

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
            require_write_access(args.device, device, "sudo-exec")
            if device.get("driver") != "paramiko":
                die(
                    "'sudo-exec' requires a Paramiko device"
                )

            ssh_sudo_exec(
                device,
                args.command,
            )

        elif args.action == "config":
            require_write_access(args.device, device, "config")
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
            require_write_access(args.device, device, "config-file")
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
            require_write_access(args.device, device, "upload")
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
            if access_mode(device) == "read_only" and args.method.upper() not in {"GET", "HEAD"}:
                die(
                    f"Device '{args.device}' is read_only; HTTP {args.method.upper()} is blocked",
                    91,
                )
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

        elif args.action == "read-file":
            read_remote_file(
                args.device,
                device,
                args.remote_file,
            )

        elif args.action == "diff-file":
            diff_remote_file(
                args.device,
                device,
                args.local_file,
                args.remote_file,
            )

        elif args.action == "backup-file":
            backup_remote_file(
                args.device,
                device,
                args.remote_file,
            )

        elif args.action == "validate":
            validate_remote(
                args.device,
                device,
                args.command,
            )

        elif args.action == "service":
            service_action(
                args.device,
                device,
                args.service,
                args.action,
            )

        elif args.action == "deploy-file":
            deploy_remote_file(
                args.device,
                device,
                args.local_file,
                args.remote_file,
                owner=args.owner,
                group=args.group,
                mode=args.mode,
                validate_command=args.validate,
                service=args.service,
                service_action_name=args.service_action,
            )

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
