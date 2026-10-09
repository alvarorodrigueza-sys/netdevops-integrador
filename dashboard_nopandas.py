import html
import math
from datetime import datetime

import streamlit as st

from netdevops_dashboard_phase3.app.config import DB_PATH, THRESHOLD_PERCENT
from netdevops_dashboard_phase3.app.database import connection, init_db


st.set_page_config(
    page_title="NetDevOps Control Center",
    page_icon="🛰️",
    layout="wide",
)

init_db()


def query_rows(sql, params=()):
    with connection() as con:
        rows = con.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def fmt_rate(value):
    value = float(value or 0)
    units = ["bps", "Kbps", "Mbps", "Gbps"]
    i = 0
    while value >= 1000 and i < len(units) - 1:
        value /= 1000.0
        i += 1
    return f"{value:.2f} {units[i]}"


def fmt_speed(value):
    return fmt_rate(value)


def fmt_time(epoch):
    if not epoch:
        return "-"
    return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")


def fmt_bytes(value):
    value = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024.0
        i += 1
    return f"{value:.2f} {units[i]}"


def safe(value):
    if value is None:
        return "-"
    return html.escape(str(value))


def render_table(rows, columns):
    if not rows:
        st.info("Sin datos disponibles.")
        return

    header = "".join(
        f"<th>{html.escape(label)}</th>"
        for _, label in columns
    )

    body_parts = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "-")
            cells.append(f"<td>{safe(value)}</td>")
        body_parts.append("<tr>" + "".join(cells) + "</tr>")

    table_html = f"""
    <div style="overflow-x:auto">
      <table style="
          width:100%;
          border-collapse:collapse;
          font-size:0.92rem;
      ">
        <thead>
          <tr>{header}</tr>
        </thead>
        <tbody>
          {''.join(body_parts)}
        </tbody>
      </table>
    </div>

    <style>
    table th, table td {{
        padding: 0.50rem 0.65rem;
        border-bottom: 1px solid rgba(128,128,128,0.25);
        text-align: left;
        white-space: nowrap;
    }}
    table th {{
        font-weight: 600;
    }}
    </style>
    """

    st.markdown(table_html, unsafe_allow_html=True)


def svg_line_chart(values, title, y_label, fixed_max=None):
    if not values:
        st.info("Todavía no hay suficientes muestras.")
        return

    width = 1000
    height = 300
    left = 70
    right = 30
    top = 35
    bottom = 55

    plot_w = width - left - right
    plot_h = height - top - bottom

    ys = [float(item["value"] or 0) for item in values]

    y_min = 0.0
    if fixed_max is not None:
        y_max = float(fixed_max)
    else:
        observed = max(ys) if ys else 0.0
        y_max = max(observed * 1.15, 1.0)

    if y_max <= y_min:
        y_max = y_min + 1.0

    count = len(values)

    def x_pos(index):
        if count <= 1:
            return left + plot_w / 2
        return left + (index / (count - 1)) * plot_w

    def y_pos(value):
        ratio = (float(value) - y_min) / (y_max - y_min)
        ratio = max(0.0, min(1.0, ratio))
        return top + plot_h - ratio * plot_h

    points = " ".join(
        f"{x_pos(i):.1f},{y_pos(item['value']):.1f}"
        for i, item in enumerate(values)
    )

    grid_parts = []
    for step in range(6):
        val = y_min + (y_max - y_min) * step / 5
        y = y_pos(val)
        grid_parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            f'stroke="currentColor" opacity="0.12" />'
        )
        grid_parts.append(
            f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" '
            f'font-size="12" fill="currentColor" opacity="0.75">{val:.2f}</text>'
        )

    threshold_line = ""
    if y_label == "Utilización (%)":
        threshold_y = y_pos(THRESHOLD_PERCENT)
        threshold_line = (
            f'<line x1="{left}" y1="{threshold_y:.1f}" '
            f'x2="{width-right}" y2="{threshold_y:.1f}" '
            f'stroke="currentColor" stroke-dasharray="8 6" opacity="0.55" />'
            f'<text x="{width-right-5}" y="{threshold_y-7:.1f}" '
            f'text-anchor="end" font-size="12" fill="currentColor" opacity="0.8">'
            f'Umbral {THRESHOLD_PERCENT:.0f}%</text>'
        )

    start_time = fmt_time(values[0]["timestamp"])[11:]
    end_time = fmt_time(values[-1]["timestamp"])[11:]

    svg = f"""
    <div style="margin-top:0.5rem; margin-bottom:1rem;">
      <div style="font-weight:600; margin-bottom:0.25rem;">{html.escape(title)}</div>
      <svg viewBox="0 0 {width} {height}" width="100%" role="img"
           aria-label="{html.escape(title)}">
        {''.join(grid_parts)}
        {threshold_line}

        <line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"
              stroke="currentColor" opacity="0.45" />
        <line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"
              stroke="currentColor" opacity="0.45" />

        <polyline points="{points}"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="3"
                  stroke-linejoin="round"
                  stroke-linecap="round" />

        <text x="{left}" y="{height-20}" font-size="12"
              fill="currentColor" opacity="0.75">{html.escape(start_time)}</text>
        <text x="{width-right}" y="{height-20}" text-anchor="end"
              font-size="12" fill="currentColor" opacity="0.75">{html.escape(end_time)}</text>

        <text x="18" y="{top + plot_h/2}"
              transform="rotate(-90 18 {top + plot_h/2})"
              text-anchor="middle" font-size="12"
              fill="currentColor" opacity="0.8">{html.escape(y_label)}</text>
      </svg>
    </div>
    """

    st.markdown(svg, unsafe_allow_html=True)


st.title("NetDevOps Control Center")
st.caption(
    "Cisco IOS-XE vía NETCONF/YANG · FortiGate vía REST API · "
    "dispositivos compartidos en modo estrictamente read-only"
)


@st.fragment(run_every="5s")
def live_dashboard():
    devices = query_rows(
        "SELECT * FROM devices ORDER BY device"
    )

    states = query_rows(
        """
        SELECT *
        FROM interface_state
        ORDER BY device, interface
        """
    )

    latest = query_rows(
        """
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
        """
    )

    alerts = query_rows(
        """
        SELECT *
        FROM alerts
        ORDER BY opened_at DESC
        LIMIT 100
        """
    )

    online = sum(1 for d in devices if d.get("status") == "online")
    active_ifaces = sum(
        1 for i in states if i.get("oper_status") == "up"
    )
    max_util = max(
        [float(x.get("utilization_pct") or 0) for x in latest],
        default=0.0,
    )
    active_alerts = sum(
        1 for a in alerts if a.get("status") == "detected"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Dispositivos online",
        f"{online}/{len(devices) if devices else 3}",
    )
    c2.metric("Interfaces UP", active_ifaces)
    c3.metric("Utilización máxima", f"{max_util:.4f}%")
    c4.metric("Alertas activas", active_alerts)

    tabs = st.tabs(
        ["Resumen", "Interfaces", "Telemetría", "Alertas"]
    )

    with tabs[0]:
        st.subheader("Estado de dispositivos")

        display_devices = []
        for row in devices:
            display_devices.append(
                {
                    "device": row.get("device"),
                    "hostname": row.get("hostname"),
                    "vendor": row.get("vendor"),
                    "host": row.get("host"),
                    "model": row.get("model"),
                    "version": row.get("version"),
                    "status": row.get("status"),
                    "last_seen": fmt_time(row.get("last_seen")),
                }
            )

        render_table(
            display_devices,
            [
                ("device", "Dispositivo"),
                ("hostname", "Hostname"),
                ("vendor", "Vendor"),
                ("host", "IP"),
                ("model", "Modelo"),
                ("version", "Versión"),
                ("status", "Estado"),
                ("last_seen", "Última lectura"),
            ],
        )

        st.subheader("Utilización actual")

        current_rows = []
        for row in latest:
            current_rows.append(
                {
                    "device": row.get("device"),
                    "hostname": row.get("hostname"),
                    "interface": row.get("interface"),
                    "traffic": fmt_rate(row.get("traffic_bps")),
                    "utilization": f"{float(row.get('utilization_pct') or 0):.4f}%",
                    "rx_errors": row.get("rx_errors"),
                    "tx_errors": row.get("tx_errors"),
                    "threshold": "SÍ" if row.get("threshold_exceeded") else "No",
                }
            )

        render_table(
            current_rows,
            [
                ("device", "Dispositivo"),
                ("hostname", "Hostname"),
                ("interface", "Interfaz"),
                ("traffic", "Tráfico"),
                ("utilization", "Utilización"),
                ("rx_errors", "RX errors"),
                ("tx_errors", "TX errors"),
                ("threshold", ">70%"),
            ],
        )

    with tabs[1]:
        interface_rows = []
        for row in states:
            interface_rows.append(
                {
                    "device": row.get("device"),
                    "interface": row.get("interface"),
                    "admin": row.get("admin_status"),
                    "oper": row.get("oper_status"),
                    "speed": fmt_speed(row.get("speed_bps")),
                    "mac": row.get("phys_address"),
                    "ip": row.get("ip_address"),
                    "rx": fmt_bytes(row.get("rx_bytes")),
                    "tx": fmt_bytes(row.get("tx_bytes")),
                    "rx_errors": row.get("rx_errors"),
                    "tx_errors": row.get("tx_errors"),
                }
            )

        render_table(
            interface_rows,
            [
                ("device", "Dispositivo"),
                ("interface", "Interfaz"),
                ("admin", "Admin"),
                ("oper", "Oper"),
                ("speed", "Velocidad"),
                ("mac", "MAC"),
                ("ip", "IP"),
                ("rx", "RX"),
                ("tx", "TX"),
                ("rx_errors", "RX errors"),
                ("tx_errors", "TX errors"),
            ],
        )

    with tabs[2]:
        devices_list = sorted(
            {row.get("device") for row in states if row.get("device")}
        )

        if not devices_list:
            st.info("Todavía no hay interfaces registradas.")
        else:
            selected_device = st.selectbox(
                "Dispositivo",
                devices_list,
                key="tele_device",
            )

            iface_list = sorted(
                {
                    row.get("interface")
                    for row in states
                    if row.get("device") == selected_device
                    and row.get("interface")
                }
            )

            if not iface_list:
                st.info("Sin interfaces para este dispositivo.")
            else:
                selected_iface = st.selectbox(
                    "Interfaz",
                    iface_list,
                    key="tele_iface",
                )

                history = query_rows(
                    """
                    SELECT timestamp, traffic_bps, utilization_pct
                    FROM telemetry
                    WHERE device=? AND interface=?
                    ORDER BY timestamp DESC
                    LIMIT 300
                    """,
                    (selected_device, selected_iface),
                )

                history.reverse()

                if not history:
                    st.info(
                        "Se necesitan al menos dos snapshots "
                        "para calcular telemetría."
                    )
                else:
                    st.write(
                        f"Umbral configurado: "
                        f"**{THRESHOLD_PERCENT:.0f}%**"
                    )

                    util_values = [
                        {
                            "timestamp": row["timestamp"],
                            "value": float(
                                row.get("utilization_pct") or 0
                            ),
                        }
                        for row in history
                    ]

                    traffic_values = [
                        {
                            "timestamp": row["timestamp"],
                            "value": float(
                                row.get("traffic_bps") or 0
                            ),
                        }
                        for row in history
                    ]

                    svg_line_chart(
                        util_values,
                        "Utilización histórica",
                        "Utilización (%)",
                        fixed_max=100.0,
                    )

                    svg_line_chart(
                        traffic_values,
                        "Tráfico histórico",
                        "Tráfico (bps)",
                    )

                    latest_row = history[-1]
                    a, b, c = st.columns(3)
                    a.metric(
                        "Utilización actual",
                        f"{float(latest_row.get('utilization_pct') or 0):.4f}%",
                    )
                    b.metric(
                        "Tráfico actual",
                        fmt_rate(latest_row.get("traffic_bps")),
                    )
                    c.metric(
                        "Muestras",
                        len(history),
                    )

    with tabs[3]:
        if not alerts:
            st.success("No hay alertas registradas.")
        else:
            alert_rows = []
            for row in alerts:
                alert_rows.append(
                    {
                        "opened": fmt_time(row.get("opened_at")),
                        "device": row.get("device"),
                        "interface": row.get("interface"),
                        "utilization": f"{float(row.get('utilization_pct') or 0):.4f}%",
                        "threshold": f"{float(row.get('threshold_pct') or 0):.0f}%",
                        "status": row.get("status"),
                        "action": row.get("action"),
                        "closed": fmt_time(row.get("closed_at")),
                    }
                )

            render_table(
                alert_rows,
                [
                    ("opened", "Inicio"),
                    ("device", "Dispositivo"),
                    ("interface", "Interfaz"),
                    ("utilization", "Utilización"),
                    ("threshold", "Umbral"),
                    ("status", "Estado"),
                    ("action", "Acción"),
                    ("closed", "Cierre"),
                ],
            )


live_dashboard()

st.caption(f"Base de datos: {DB_PATH}")
