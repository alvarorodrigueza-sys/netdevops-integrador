from flask import Flask, jsonify, Response, request
import sqlite3
from pathlib import Path
import subprocess
import threading
import tempfile
import time
import os
import shlex
import socket
import re
from datetime import datetime, timezone

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DB_PATH = PROJECT_ROOT / "netdevops_dashboard_phase3" / "data" / "netdevops.db"
HTML_PATH = APP_DIR / "index.html"

ANSIBLE_DIR_WSL = "/mnt/c/lab-agent/ansible"
ANSIBLE_CONFIG_WSL = "/mnt/c/lab-agent/ansible/ansible.cfg"
THRESHOLD_PERCENT = 70.0
LIVE_WRITES_ENABLED = os.getenv("NETDEVOPS_ENABLE_LIVE_WRITES", "0") == "1"
LIVE_DEVICE = os.getenv("NETDEVOPS_LIVE_DEVICE", "").strip()
LIVE_INTERFACE = os.getenv("NETDEVOPS_LIVE_INTERFACE", "").strip()

app = Flask(__name__)

AUTOMATION_ACTIONS = {
    "deploy_web": ("Desplegar WEB", "site.yml", "web"),
    "deploy_ftp": ("Desplegar FTP", "site.yml", "ftp"),
    "deploy_mail": ("Desplegar MAIL", "site.yml", "mail"),
    "deploy_dns": ("Desplegar DNS", "site.yml", "dns"),
    "deploy_all": ("Desplegar TODOS", "site.yml", None),
    "verify_all": ("Verificar servicios", "verify.yml", None),
    "verify_web": ("Verificar WEB", "verify.yml", "web"),
    "verify_ftp": ("Verificar FTP", "verify.yml", "ftp"),
    "verify_mail": ("Verificar MAIL", "verify.yml", "mail"),
    "verify_dns": ("Verificar DNS", "verify.yml", "dns"),
}

SERVICE_ACTIONS = {
    "web": {
        "label": "WEB / NGINX",
        "group": "web",
        "host": "web01",
        "ports": [80],
    },
    "ftp": {
        "label": "FTP / VSFTPD",
        "group": "ftp",
        "host": "ftp01",
        "ports": [21],
    },
    "mail": {
        "label": "MAIL / Postfix + Dovecot",
        "group": "mail",
        "host": "mail01",
        "ports": [25, 143],
    },
    "dns": {
        "label": "DNS / BIND9",
        "group": "dns",
        "host": "dns01",
        "ports": [53],
    },
}

def connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def db_rows(sql, params=()):
    con = connection()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

def db_one(sql, params=()):
    con = connection()
    try:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        con.close()

def init_control_tables():
    con = connection()
    try:
        con.executescript(
            '''
            CREATE TABLE IF NOT EXISTS automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                finished_at REAL,
                action TEXT NOT NULL,
                label TEXT NOT NULL,
                target TEXT,
                initiated_by TEXT NOT NULL,
                status TEXT NOT NULL,
                return_code INTEGER,
                log_text TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS demo_authorization (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                device TEXT,
                interface TEXT,
                authorized_by TEXT,
                armed INTEGER NOT NULL DEFAULT 0,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS mitigation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device TEXT NOT NULL,
                interface TEXT NOT NULL,
                action TEXT NOT NULL,
                authorized_by TEXT NOT NULL,
                reason TEXT,
                result TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mitigation_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                device TEXT NOT NULL,
                interface TEXT NOT NULL,
                utilization_pct REAL NOT NULL,
                threshold_pct REAL NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                authorized_by TEXT NOT NULL,
                mitigation_result TEXT,
                recovery_result TEXT
            );
            '''
        )
        con.commit()
    finally:
        con.close()

def latest_telemetry():
    return db_rows(
        '''
        SELECT t.*
        FROM telemetry t
        JOIN (
            SELECT device, interface, MAX(timestamp) AS max_ts
            FROM telemetry
            GROUP BY device, interface
        ) x
          ON t.device=x.device
         AND t.interface=x.interface
         AND t.timestamp=x.max_ts
        ORDER BY t.device, t.interface
        '''
    )


def load_ansible_inventory_hosts():
    inventory = PROJECT_ROOT / "ansible" / "inventory.ini"
    result = {}
    if not inventory.exists():
        return result

    current_group = None
    for raw in inventory.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1]
            continue
        if current_group in {"web", "ftp", "mail", "dns"}:
            parts = line.split()
            if not parts:
                continue
            hostname = parts[0]
            ip = None
            for part in parts[1:]:
                if part.startswith("ansible_host="):
                    ip = part.split("=", 1)[1]
                    break
            if ip:
                result[current_group] = {"inventory_host": hostname, "ip": ip}
    return result


def tcp_probe(ip, port, timeout=1.0):
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def service_status_snapshot():
    inventory = load_ansible_inventory_hosts()
    rows = []
    for key, spec in SERVICE_ACTIONS.items():
        inv = inventory.get(key, {})
        ip = inv.get("ip")
        ports = {}
        for port in spec["ports"]:
            ports[str(port)] = bool(ip and tcp_probe(ip, port))
        healthy = bool(ip) and all(ports.values())
        rows.append({
            "key": key,
            "label": spec["label"],
            "host": inv.get("inventory_host", spec["host"]),
            "ip": ip,
            "ports": ports,
            "healthy": healthy,
        })
    return rows


def windows_to_wsl(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    raise RuntimeError(f"No se pudo convertir a WSL: {path}")

def append_run_log(run_id, text):
    con = connection()
    try:
        row = con.execute(
            "SELECT log_text FROM automation_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        current = row["log_text"] if row else ""
        con.execute(
            "UPDATE automation_runs SET log_text=? WHERE id=?",
            (current + text, run_id),
        )
        con.commit()
    finally:
        con.close()

def run_ansible_job(run_id, action, vault_password):
    label, playbook, limit = AUTOMATION_ACTIONS[action]
    try:
        # Use a dedicated Bash helper instead of a complex `bash -lc` command.
        # This avoids Windows/WSL quoting problems and keeps the Vault file
        # inside the Linux filesystem (/tmp).
        runner_wsl = windows_to_wsl(APP_DIR / "ansible_runner.sh")

        append_run_log(run_id, f"$ {label}\n\n")

        proc = subprocess.Popen(
            [
                "wsl.exe",
                "bash",
                runner_wsl,
                playbook,
                limit or "all",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Password travels only through stdin and is never written to SQLite/logs.
        assert proc.stdin is not None
        proc.stdin.write(vault_password + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        assert proc.stdout is not None
        for line in proc.stdout:
            append_run_log(run_id, line)

        rc = proc.wait()

        con = connection()
        try:
            con.execute(
                "UPDATE automation_runs SET finished_at=?,status=?,return_code=? WHERE id=?",
                (time.time(), "success" if rc == 0 else "failed", rc, run_id),
            )
            con.commit()
        finally:
            con.close()

    except Exception as exc:
        append_run_log(run_id, f"\n[ERROR] {type(exc).__name__}: {exc}\n")
        con = connection()
        try:
            con.execute(
                "UPDATE automation_runs SET finished_at=?,status='failed',return_code=-1 WHERE id=?",
                (time.time(), run_id),
            )
            con.commit()
        finally:
            con.close()


def run_service_job(run_id, group, operation, vault_password):
    tmp = None
    try:
        runner_wsl = windows_to_wsl(APP_DIR / "server_runner.sh")
        append_run_log(run_id, f"$ {operation.upper()} {group.upper()}\n\n")

        proc = subprocess.Popen(
            ["wsl.exe", "bash", runner_wsl, group, operation],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert proc.stdin is not None
        proc.stdin.write(vault_password + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        assert proc.stdout is not None
        for line in proc.stdout:
            append_run_log(run_id, line)

        rc = proc.wait()
        con = connection()
        try:
            con.execute(
                "UPDATE automation_runs SET finished_at=?,status=?,return_code=? WHERE id=?",
                (time.time(), "success" if rc == 0 else "failed", rc, run_id),
            )
            con.commit()
        finally:
            con.close()

    except Exception as exc:
        append_run_log(run_id, f"\n[ERROR] {type(exc).__name__}: {exc}\n")
        con = connection()
        try:
            con.execute(
                "UPDATE automation_runs SET finished_at=?,status='failed',return_code=-1 WHERE id=?",
                (time.time(), run_id),
            )
            con.commit()
        finally:
            con.close()


@app.get("/api/servers/status")
def api_servers_status():
    return jsonify(service_status_snapshot())


@app.post("/api/service/run")
def api_service_run():
    data = request.get_json(silent=True) or {}
    group = str(data.get("group", "")).strip()
    operation = str(data.get("operation", "")).strip()
    vault_password = str(data.get("vault_password", ""))
    initiated_by = str(data.get("initiated_by", "")).strip() or "dashboard"

    if group not in SERVICE_ACTIONS:
        return jsonify({"ok": False, "error": "Servidor no permitido"}), 400
    if operation not in {"logs", "restart"}:
        return jsonify({"ok": False, "error": "Operación no permitida"}), 400
    if not vault_password:
        return jsonify({"ok": False, "error": "Ingresa la contraseña de Ansible Vault"}), 400

    label = (
        f"Logs {SERVICE_ACTIONS[group]['label']}"
        if operation == "logs"
        else f"Reiniciar {SERVICE_ACTIONS[group]['label']}"
    )

    con = connection()
    try:
        cur = con.execute(
            "INSERT INTO automation_runs(started_at,action,label,target,initiated_by,status,log_text) "
            "VALUES(?,?,?,?,?,'running','')",
            (
                time.time(),
                f"server_{operation}_{group}",
                label,
                group,
                initiated_by,
            ),
        )
        run_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    threading.Thread(
        target=run_service_job,
        args=(run_id, group, operation, vault_password),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "run_id": run_id})



def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def active_incident(device, interface):
    return db_one(
        """
        SELECT *
        FROM mitigation_incidents
        WHERE device=? AND interface=?
          AND status IN ('DRY_RUN_MITIGATED','MITIGATED')
        ORDER BY id DESC
        LIMIT 1
        """,
        (device, interface),
    )


def latest_exact_telemetry(device, interface):
    return db_one(
        """
        SELECT *
        FROM telemetry
        WHERE device=? AND interface=?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (device, interface),
    )


def live_guard(device, interface):
    if not LIVE_WRITES_ENABLED:
        return False, "LIVE_WRITES_DISABLED"
    if not LIVE_DEVICE or not LIVE_INTERFACE:
        return False, "LIVE_TARGET_NOT_CONFIGURED"
    if device != LIVE_DEVICE or interface != LIVE_INTERFACE:
        return False, "LIVE_TARGET_MISMATCH"
    auth = db_one("SELECT * FROM demo_authorization WHERE id=1")
    if not auth or not int(auth.get("armed") or 0):
        return False, "PORT_NOT_ARMED"
    if auth["device"] != device or auth["interface"] != interface:
        return False, "AUTHORIZED_PORT_MISMATCH"
    return True, "OK"


def load_secret_env():
    candidates = [
        Path.home() / ".lab-agent" / "secrets.env",
        PROJECT_ROOT / "secrets.env",
    ]
    values = {}
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def perform_netconf_interface_action(device, interface, enabled):
    allowed, reason = live_guard(device, interface)
    if not allowed:
        return False, reason

    secrets = load_secret_env()
    username = secrets.get("NETDEVOPS_CISCO_USERNAME") or os.getenv("NETDEVOPS_CISCO_USERNAME")
    password = secrets.get("NETDEVOPS_CISCO_PASSWORD") or os.getenv("NETDEVOPS_CISCO_PASSWORD")
    if not username or not password:
        return False, "LIVE_CREDENTIALS_MISSING"

    hosts = {
        "cisco01": "172.23.18.224",
        "cisco02": "172.23.18.223",
    }
    host = hosts.get(device)
    if not host:
        return False, "UNSUPPORTED_DEVICE"

    try:
        from ncclient import manager

        enabled_text = "true" if enabled else "false"
        config = f"""
        <config>
          <interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">
            <interface>
              <name>{interface}</name>
              <enabled>{enabled_text}</enabled>
            </interface>
          </interfaces>
        </config>
        """

        with manager.connect(
            host=host,
            port=830,
            username=username,
            password=password,
            hostkey_verify=False,
            allow_agent=False,
            look_for_keys=False,
            timeout=20,
        ) as session:
            reply = session.edit_config(target="running", config=config)

        if getattr(reply, "ok", False):
            return True, "NETCONF_OK"
        return False, "NETCONF_REPLY_NOT_OK"

    except Exception as exc:
        return False, f"NETCONF_ERROR:{type(exc).__name__}:{exc}"


def create_incident(device, interface, utilization, user, mode, result):
    con = connection()
    try:
        cur = con.execute(
            """
            INSERT INTO mitigation_incidents(
                opened_at,device,interface,utilization_pct,threshold_pct,
                status,mode,authorized_by,mitigation_result
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                utc_now_iso(),
                device,
                interface,
                float(utilization),
                THRESHOLD_PERCENT,
                "MITIGATED" if mode == "LIVE" else "DRY_RUN_MITIGATED",
                mode,
                user,
                result,
            ),
        )
        incident_id = cur.lastrowid
        con.commit()
        return incident_id
    finally:
        con.close()


def evaluate_authorized_port(force_utilization=None):
    auth = db_one("SELECT * FROM demo_authorization WHERE id=1")
    if not auth or not int(auth.get("armed") or 0):
        return {"triggered": False, "reason": "NOT_ARMED"}

    device = auth["device"]
    interface = auth["interface"]
    user = auth["authorized_by"]

    if active_incident(device, interface):
        return {"triggered": False, "reason": "INCIDENT_ALREADY_ACTIVE"}

    if force_utilization is None:
        sample = latest_exact_telemetry(device, interface)
        if not sample:
            return {"triggered": False, "reason": "NO_TELEMETRY"}
        utilization = float(sample.get("utilization_pct") or 0)
    else:
        utilization = float(force_utilization)

    if utilization < THRESHOLD_PERCENT:
        return {
            "triggered": False,
            "reason": "BELOW_THRESHOLD",
            "utilization": utilization,
        }

    if LIVE_WRITES_ENABLED:
        ok, result = perform_netconf_interface_action(device, interface, enabled=False)
        mode = "LIVE"
        action_result = result
        if not ok:
            audit_mitigation(
                device, interface, "shutdown", user,
                f"Utilización {utilization:.2f}% >= {THRESHOLD_PERCENT:.0f}%",
                result,
            )
            return {
                "triggered": False,
                "reason": result,
                "utilization": utilization,
            }
    else:
        mode = "DRY_RUN"
        action_result = "DRY_RUN_THRESHOLD_DETECTED"

    incident_id = create_incident(
        device, interface, utilization, user, mode, action_result
    )
    audit_mitigation(
        device, interface, "shutdown", user,
        f"Utilización {utilization:.2f}% >= {THRESHOLD_PERCENT:.0f}%",
        action_result,
    )
    return {
        "triggered": True,
        "mode": mode,
        "incident_id": incident_id,
        "device": device,
        "interface": interface,
        "utilization": utilization,
        "result": action_result,
    }


def mitigation_watch_loop():
    while True:
        try:
            evaluate_authorized_port()
        except Exception:
            # Watcher must never crash the dashboard.
            pass
        time.sleep(5)


@app.get("/api/state")
def api_state():
    return jsonify({
        "threshold": THRESHOLD_PERCENT,
        "live_writes_enabled": LIVE_WRITES_ENABLED,
        "devices": db_rows("SELECT * FROM devices ORDER BY device"),
        "interfaces": db_rows("SELECT * FROM interface_state ORDER BY device,interface"),
        "latest": latest_telemetry(),
        "alerts": db_rows("SELECT * FROM alerts ORDER BY opened_at DESC LIMIT 100"),
        "authorization": db_one("SELECT * FROM demo_authorization WHERE id=1"),
        "mitigation_actions": db_rows("SELECT * FROM mitigation_actions ORDER BY id DESC LIMIT 100"),
        "mitigation_incidents": db_rows(
            "SELECT * FROM mitigation_incidents ORDER BY id DESC LIMIT 100"
        ),
        "live_target": {
            "device": LIVE_DEVICE,
            "interface": LIVE_INTERFACE,
        },
        "automation_runs": db_rows(
            "SELECT id,started_at,finished_at,action,label,target,initiated_by,status,return_code "
            "FROM automation_runs ORDER BY id DESC LIMIT 30"
        ),
    })

@app.get("/api/history")
def api_history():
    device = request.args.get("device", "")
    interface = request.args.get("interface", "")
    if not device or not interface:
        return jsonify([])
    rows = db_rows(
        "SELECT timestamp,traffic_bps,utilization_pct FROM telemetry "
        "WHERE device=? AND interface=? ORDER BY timestamp DESC LIMIT 300",
        (device, interface),
    )
    rows.reverse()
    return jsonify(rows)

@app.post("/api/automation/run")
def api_automation_run():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", ""))
    vault_password = str(data.get("vault_password", ""))
    initiated_by = str(data.get("initiated_by", "")).strip() or "dashboard"

    if action not in AUTOMATION_ACTIONS:
        return jsonify({"ok": False, "error": "Acción no permitida"}), 400
    if not vault_password:
        return jsonify({"ok": False, "error": "Ingresa la contraseña de Ansible Vault"}), 400

    label, _, limit = AUTOMATION_ACTIONS[action]
    con = connection()
    try:
        cur = con.execute(
            "INSERT INTO automation_runs(started_at,action,label,target,initiated_by,status,log_text) "
            "VALUES(?,?,?,?,?,'running','')",
            (time.time(), action, label, limit or "all", initiated_by),
        )
        run_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    threading.Thread(
        target=run_ansible_job,
        args=(run_id, action, vault_password),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "run_id": run_id})

@app.get("/api/automation/run/<int:run_id>")
def api_automation_detail(run_id):
    row = db_one("SELECT * FROM automation_runs WHERE id=?", (run_id,))
    if not row:
        return jsonify({"error": "Run no encontrado"}), 404
    return jsonify(row)

def validate_demo_target(device, interface):
    if device not in {"cisco01", "cisco02"}:
        raise ValueError("Solo cisco01/cisco02 pueden prepararse para la demostración")
    row = db_one(
        "SELECT device,interface FROM interface_state WHERE device=? AND interface=?",
        (device, interface),
    )
    if not row:
        raise ValueError("La interfaz no existe en la telemetría actual")

@app.post("/api/mitigation/authorize")
def api_mitigation_authorize():
    data = request.get_json(silent=True) or {}
    device = str(data.get("device", "")).strip()
    interface = str(data.get("interface", "")).strip()
    user = str(data.get("authorized_by", "")).strip()
    if not user:
        return jsonify({"ok": False, "error": "Indica el usuario autorizador"}), 400
    try:
        validate_demo_target(device, interface)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    con = connection()
    try:
        con.execute(
            '''
            INSERT INTO demo_authorization(id,device,interface,authorized_by,armed,updated_at)
            VALUES(1,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                device=excluded.device,
                interface=excluded.interface,
                authorized_by=excluded.authorized_by,
                armed=excluded.armed,
                updated_at=excluded.updated_at
            ''',
            (device, interface, user, 1, time.time()),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "message": f"Puerto autorizado: {device} {interface}"})

@app.post("/api/mitigation/disarm")
def api_mitigation_disarm():
    con = connection()
    try:
        con.execute(
            "UPDATE demo_authorization SET armed=0,updated_at=? WHERE id=1",
            (time.time(),),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})

def audit_mitigation(device, interface, action, user, reason, result):
    con = connection()
    try:
        con.execute(
            "INSERT INTO mitigation_actions(timestamp,device,interface,action,authorized_by,reason,result) "
            "VALUES(datetime('now'),?,?,?,?,?,?)",
            (device, interface, action, user, reason, result),
        )
        con.commit()
    finally:
        con.close()

@app.post("/api/mitigation/simulate")
def api_mitigation_simulate():
    result = evaluate_authorized_port(force_utilization=80.0)
    if not result.get("triggered"):
        return jsonify({"ok": False, "error": result.get("reason", "No activado")}), 400
    return jsonify({
        "ok": True,
        "message": (
            f"Incidente #{result['incident_id']} creado en {result['mode']}. "
            f"Utilización simulada: {result['utilization']:.1f}%."
        ),
        "incident": result,
    })


@app.post("/api/mitigation/recover")
def api_mitigation_recover():
    data = request.get_json(silent=True) or {}
    incident_id = int(data.get("incident_id") or 0)
    user = str(data.get("authorized_by", "")).strip()

    if not incident_id:
        return jsonify({"ok": False, "error": "Incidente inválido"}), 400
    if not user:
        return jsonify({"ok": False, "error": "Indica el usuario autorizador"}), 400

    incident = db_one(
        "SELECT * FROM mitigation_incidents WHERE id=?",
        (incident_id,),
    )
    if not incident:
        return jsonify({"ok": False, "error": "Incidente no encontrado"}), 404
    if incident["status"] not in ("MITIGATED", "DRY_RUN_MITIGATED"):
        return jsonify({"ok": False, "error": "El incidente ya está cerrado"}), 400

    device = incident["device"]
    interface = incident["interface"]

    if incident["mode"] == "LIVE":
        ok, result = perform_netconf_interface_action(
            device, interface, enabled=True
        )
        if not ok:
            audit_mitigation(
                device, interface, "no shutdown", user,
                "Recuperación solicitada desde dashboard",
                result,
            )
            return jsonify({"ok": False, "error": result}), 500
        recovery_result = result
    else:
        recovery_result = "DRY_RUN_RECOVERY"

    audit_mitigation(
        device, interface, "no shutdown", user,
        "Recuperación solicitada desde dashboard",
        recovery_result,
    )

    con = connection()
    try:
        con.execute(
            """
            UPDATE mitigation_incidents
            SET closed_at=?, status='RECOVERED', recovery_result=?
            WHERE id=?
            """,
            (utc_now_iso(), recovery_result, incident_id),
        )
        con.commit()
    finally:
        con.close()

    return jsonify({
        "ok": True,
        "message": f"Incidente #{incident_id} recuperado: {recovery_result}",
    })

@app.get("/")
def index():
    if not DB_PATH.exists():
        return Response(f"<h2>No se encontró la base de datos</h2><p>{DB_PATH}</p>", status=500)
    return Response(HTML_PATH.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")

if __name__ == "__main__":
    init_control_tables()
    threading.Thread(target=mitigation_watch_loop, daemon=True).start()
    print(f"[INFO] SQLite: {DB_PATH}")
    print(f"[INFO] LIVE writes: {LIVE_WRITES_ENABLED}")
    print(f"[INFO] LIVE target: {LIVE_DEVICE or '-'} {LIVE_INTERFACE or '-'}")
    print("[INFO] Dashboard: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
