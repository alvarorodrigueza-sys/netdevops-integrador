import argparse
import json
import subprocess
import sys
import time

from .config import (
    DEVICES,
    LABCTL_CMD,
    POLL_INTERVAL_SECONDS,
    THRESHOLD_PERCENT,
)
from .database import connection, init_db


def extract_json_objects(text: str):
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)

    while idx < length:
        start = text.find("{", idx)
        if start == -1:
            break

        try:
            obj, consumed = decoder.raw_decode(text[start:])
            yield obj
            idx = start + consumed
        except json.JSONDecodeError:
            idx = start + 1


def parse_snapshot_output(stdout: str) -> dict:
    candidates = []

    for obj in extract_json_objects(stdout):
        if not isinstance(obj, dict):
            continue

        if obj.get("type") == "snapshot" and isinstance(obj.get("data"), dict):
            return obj["data"]

        if "device" in obj and isinstance(obj.get("interfaces"), list):
            candidates.append(obj)

    if candidates:
        return candidates[-1]

    preview = stdout.strip()
    if len(preview) > 1200:
        preview = preview[:1200] + "\n...[salida truncada]..."

    raise RuntimeError(
        "No se encontró un snapshot JSON válido en la salida de labctl.\n"
        "Salida recibida:\n"
        + (preview or "<vacía>")
    )


def get_snapshot(device: str) -> dict:
    result = subprocess.run(
        [str(LABCTL_CMD), "snapshot", device],
        capture_output=True,
        text=True,
        timeout=90,
        shell=False,
    )

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"{device}: labctl retornó {result.returncode}: {msg}"
        )

    return parse_snapshot_output(result.stdout)


def upsert_device(con, snap, now):
    con.execute(
        """
        INSERT INTO devices(
            device, vendor, host, hostname, model,
            version, last_seen, status
        )
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(device) DO UPDATE SET
            vendor=excluded.vendor,
            host=excluded.host,
            hostname=excluded.hostname,
            model=excluded.model,
            version=excluded.version,
            last_seen=excluded.last_seen,
            status=excluded.status
        """,
        (
            snap.get("device"),
            snap.get("vendor"),
            snap.get("host"),
            snap.get("hostname"),
            snap.get("model"),
            snap.get("version"),
            now,
            "online",
        ),
    )


def process_snapshot(snap: dict):
    device = snap["device"]
    hostname = snap.get("hostname", device)
    now = float(snap.get("epoch") or time.time())

    with connection() as con:
        upsert_device(con, snap, now)

        for iface in snap.get("interfaces", []):
            name = iface.get("name")
            if not name:
                continue

            speed = int(iface.get("speed_bps") or 0)
            rx = int(iface.get("rx_bytes") or 0)
            tx = int(iface.get("tx_bytes") or 0)
            rx_errors = int(iface.get("rx_errors") or 0)
            tx_errors = int(iface.get("tx_errors") or 0)

            old = con.execute(
                """
                SELECT rx_bytes, tx_bytes, last_seen
                FROM interface_state
                WHERE device=? AND interface=?
                """,
                (device, name),
            ).fetchone()

            con.execute(
                """
                INSERT INTO interface_state(
                    device, interface, admin_status, oper_status,
                    speed_bps, phys_address, ip_address,
                    rx_bytes, tx_bytes, rx_errors, tx_errors, last_seen
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(device,interface) DO UPDATE SET
                    admin_status=excluded.admin_status,
                    oper_status=excluded.oper_status,
                    speed_bps=excluded.speed_bps,
                    phys_address=excluded.phys_address,
                    ip_address=excluded.ip_address,
                    rx_bytes=excluded.rx_bytes,
                    tx_bytes=excluded.tx_bytes,
                    rx_errors=excluded.rx_errors,
                    tx_errors=excluded.tx_errors,
                    last_seen=excluded.last_seen
                """,
                (
                    device,
                    name,
                    iface.get("admin_status"),
                    iface.get("oper_status"),
                    speed,
                    iface.get("phys_address"),
                    iface.get("ip_address"),
                    rx,
                    tx,
                    rx_errors,
                    tx_errors,
                    now,
                ),
            )

            if not old or speed <= 0:
                continue

            old_time = float(old["last_seen"] or 0)
            dt = now - old_time
            if dt <= 0:
                continue

            delta_rx = rx - int(old["rx_bytes"] or 0)
            delta_tx = tx - int(old["tx_bytes"] or 0)

            counter_reset = delta_rx < 0 or delta_tx < 0
            if counter_reset:
                delta_rx = 0
                delta_tx = 0

            traffic_bps = ((delta_rx + delta_tx) * 8.0) / dt
            utilization = (traffic_bps / speed) * 100.0
            exceeded = utilization >= THRESHOLD_PERCENT

            con.execute(
                """
                INSERT INTO telemetry(
                    timestamp, device, hostname, interface,
                    speed_bps, interval_seconds,
                    delta_rx_bytes, delta_tx_bytes,
                    traffic_bps, utilization_pct,
                    rx_errors, tx_errors,
                    threshold_exceeded, counter_reset
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now,
                    device,
                    hostname,
                    name,
                    speed,
                    dt,
                    delta_rx,
                    delta_tx,
                    traffic_bps,
                    utilization,
                    rx_errors,
                    tx_errors,
                    int(exceeded),
                    int(counter_reset),
                ),
            )

            if exceeded:
                existing = con.execute(
                    """
                    SELECT id
                    FROM alerts
                    WHERE device=?
                      AND interface=?
                      AND status='detected'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (device, name),
                ).fetchone()

                if not existing:
                    con.execute(
                        """
                        INSERT INTO alerts(
                            opened_at, device, interface,
                            utilization_pct, threshold_pct,
                            status, action
                        )
                        VALUES(
                            ?,?,?,?,?,
                            'detected',
                            'READ_ONLY_ALERT'
                        )
                        """,
                        (
                            now,
                            device,
                            name,
                            utilization,
                            THRESHOLD_PERCENT,
                        ),
                    )


def mark_offline(device: str):
    with connection() as con:
        con.execute(
            """
            INSERT INTO devices(device, status, last_seen)
            VALUES(?, 'offline', ?)
            ON CONFLICT(device) DO UPDATE SET
                status='offline',
                last_seen=excluded.last_seen
            """,
            (device, time.time()),
        )


def collect_once():
    success = 0

    for device in DEVICES:
        try:
            snap = get_snapshot(device)
            process_snapshot(snap)
            print(
                f"[OK] {device}: snapshot almacenado "
                f"({len(snap.get('interfaces', []))} interfaces)"
            )
            success += 1
        except Exception as exc:
            mark_offline(device)
            print(f"[FAIL] {device}: {exc}", file=sys.stderr)

    print(f"[INFO] Ciclo completado: {success}/{len(DEVICES)} dispositivos OK")


def main():
    parser = argparse.ArgumentParser(
        description="NetDevOps telemetry collector"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval",
        type=float,
        default=POLL_INTERVAL_SECONDS,
    )
    args = parser.parse_args()

    init_db()

    if not LABCTL_CMD.exists():
        raise SystemExit(
            f"No se encontró labctl.cmd en {LABCTL_CMD}. "
            "Define LABCTL_CMD si está en otra ruta."
        )

    if args.once:
        collect_once()
        return

    print(
        f"[INFO] Collector activo. "
        f"Intervalo objetivo: {args.interval}s"
    )
    print(f"[INFO] labctl: {LABCTL_CMD}")

    while True:
        cycle_start = time.monotonic()
        collect_once()

        elapsed = time.monotonic() - cycle_start
        wait = max(0.0, args.interval - elapsed)

        if wait:
            time.sleep(wait)


if __name__ == "__main__":
    main()
