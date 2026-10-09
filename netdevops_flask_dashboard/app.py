from flask import Flask, jsonify, Response, request
import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DB_PATH = PROJECT_ROOT / "netdevops_dashboard_phase3" / "data" / "netdevops.db"
THRESHOLD_PERCENT = 70.0

app = Flask(__name__)

def db_rows(sql, params=()):
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

def latest_telemetry():
    return db_rows(
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

@app.get("/api/state")
def api_state():
    devices = db_rows("SELECT * FROM devices ORDER BY device")
    interfaces = db_rows(
        "SELECT * FROM interface_state ORDER BY device, interface"
    )
    latest = latest_telemetry()
    alerts = db_rows(
        "SELECT * FROM alerts ORDER BY opened_at DESC LIMIT 100"
    )
    return jsonify({
        "threshold": THRESHOLD_PERCENT,
        "devices": devices,
        "interfaces": interfaces,
        "latest": latest,
        "alerts": alerts,
    })

@app.get("/api/history")
def api_history():
    device = request.args.get("device", "")
    interface = request.args.get("interface", "")
    if not device or not interface:
        return jsonify([])
    rows = db_rows(
        """
        SELECT timestamp, traffic_bps, utilization_pct
        FROM telemetry
        WHERE device=? AND interface=?
        ORDER BY timestamp DESC
        LIMIT 300
        """,
        (device, interface),
    )
    rows.reverse()
    return jsonify(rows)

HTML = '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>NetDevOps Control Center</title>\n<style>\n:root{\n  --bg:#0b1020;\n  --panel:#121a2c;\n  --panel2:#182238;\n  --text:#e8eefc;\n  --muted:#93a4c7;\n  --line:#26334f;\n  --ok:#2ecc71;\n  --warn:#f39c12;\n  --bad:#e74c3c;\n  --accent:#5aa9ff;\n}\n*{box-sizing:border-box}\nbody{\n  margin:0;\n  font-family:Segoe UI,Arial,sans-serif;\n  background:linear-gradient(180deg,#09111f,#0d1527);\n  color:var(--text);\n}\n.container{\n  max-width:1500px;\n  margin:auto;\n  padding:24px;\n}\nh1{margin:0 0 4px 0;font-size:30px}\n.subtitle{color:var(--muted);margin-bottom:22px}\n.cards{\n  display:grid;\n  grid-template-columns:repeat(4,minmax(180px,1fr));\n  gap:14px;\n  margin-bottom:20px;\n}\n.card,.panel{\n  background:rgba(18,26,44,.94);\n  border:1px solid var(--line);\n  border-radius:14px;\n  box-shadow:0 8px 30px rgba(0,0,0,.22);\n}\n.card{padding:18px}\n.card-label{color:var(--muted);font-size:13px}\n.card-value{font-size:28px;font-weight:700;margin-top:5px}\n.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}\n.tabbtn{\n  border:1px solid var(--line);\n  background:var(--panel2);\n  color:var(--text);\n  padding:10px 16px;\n  border-radius:9px;\n  cursor:pointer;\n}\n.tabbtn.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}\n.tab{display:none}\n.tab.active{display:block}\n.panel{padding:18px;margin-bottom:16px}\n.panel h2{font-size:18px;margin:0 0 14px}\n.table-wrap{overflow:auto;max-height:520px}\ntable{width:100%;border-collapse:collapse;font-size:13px}\nth,td{\n  text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);\n  white-space:nowrap\n}\nth{\n  position:sticky;top:0;background:var(--panel);z-index:1;color:#b9c7e5\n}\n.badge{\n  display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;\n  border:1px solid var(--line)\n}\n.up{color:#8ff0b3}.down{color:#ff9c94}.warn{color:#ffc66d}\n.controls{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}\nselect{\n  background:var(--panel2);color:var(--text);\n  border:1px solid var(--line);border-radius:8px;padding:8px 10px\n}\ncanvas{\n  width:100%;height:280px;background:#0c1424;\n  border:1px solid var(--line);border-radius:10px\n}\n.small{font-size:12px;color:var(--muted)}\n@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}}\n@media(max-width:520px){.cards{grid-template-columns:1fr}}\n</style>\n</head>\n<body>\n<div class="container">\n  <h1>NetDevOps Control Center</h1>\n  <div class="subtitle">\n    Cisco IOS-XE vía NETCONF/YANG · FortiGate vía REST API · Shared devices read-only\n  </div>\n\n  <div class="cards">\n    <div class="card"><div class="card-label">Dispositivos online</div><div id="c-devices" class="card-value">-</div></div>\n    <div class="card"><div class="card-label">Interfaces UP</div><div id="c-ifaces" class="card-value">-</div></div>\n    <div class="card"><div class="card-label">Utilización máxima</div><div id="c-util" class="card-value">-</div></div>\n    <div class="card"><div class="card-label">Alertas activas</div><div id="c-alerts" class="card-value">-</div></div>\n  </div>\n\n  <div class="tabs">\n    <button class="tabbtn active" data-tab="summary">Resumen</button>\n    <button class="tabbtn" data-tab="interfaces">Interfaces</button>\n    <button class="tabbtn" data-tab="telemetry">Telemetría</button>\n    <button class="tabbtn" data-tab="alerts">Alertas</button>\n  </div>\n\n  <div id="summary" class="tab active">\n    <div class="panel">\n      <h2>Estado de dispositivos</h2>\n      <div class="table-wrap"><table id="tbl-devices"></table></div>\n    </div>\n    <div class="panel">\n      <h2>Utilización actual</h2>\n      <div class="table-wrap"><table id="tbl-current"></table></div>\n    </div>\n  </div>\n\n  <div id="interfaces" class="tab">\n    <div class="panel">\n      <h2>Interfaces monitoreadas</h2>\n      <div class="table-wrap"><table id="tbl-interfaces"></table></div>\n    </div>\n  </div>\n\n  <div id="telemetry" class="tab">\n    <div class="panel">\n      <h2>Histórico de telemetría</h2>\n      <div class="controls">\n        <label>Dispositivo <select id="sel-device"></select></label>\n        <label>Interfaz <select id="sel-interface"></select></label>\n      </div>\n      <div class="small" id="tele-meta"></div>\n      <h3>Utilización (%)</h3>\n      <canvas id="chart-util" width="1200" height="280"></canvas>\n      <h3>Tráfico (bps)</h3>\n      <canvas id="chart-traffic" width="1200" height="280"></canvas>\n    </div>\n  </div>\n\n  <div id="alerts" class="tab">\n    <div class="panel">\n      <h2>Alertas registradas</h2>\n      <div class="table-wrap"><table id="tbl-alerts"></table></div>\n    </div>\n  </div>\n\n  <div class="small">Actualización automática cada 5 segundos · SQLite: netdevops.db</div>\n</div>\n\n<script>\nlet state = null;\n\nfunction esc(v){\n  if(v===null || v===undefined) return "-";\n  return String(v).replace(/[&<>"\']/g, m => ({\n    "&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;","\'":"&#39;"\n  }[m]));\n}\nfunction fmtTime(epoch){\n  if(!epoch) return "-";\n  return new Date(epoch*1000).toLocaleString();\n}\nfunction fmtRate(v){\n  v=Number(v||0); let u=["bps","Kbps","Mbps","Gbps"], i=0;\n  while(v>=1000 && i<u.length-1){v/=1000;i++}\n  return v.toFixed(2)+" "+u[i];\n}\nfunction fmtBytes(v){\n  v=Number(v||0); let u=["B","KB","MB","GB","TB"], i=0;\n  while(v>=1024 && i<u.length-1){v/=1024;i++}\n  return v.toFixed(2)+" "+u[i];\n}\nfunction table(el, cols, rows){\n  const head="<thead><tr>"+cols.map(c=>`<th>${esc(c[1])}</th>`).join("")+"</tr></thead>";\n  const body="<tbody>"+rows.map(r=>"<tr>"+cols.map(c=>`<td>${c[2]?c[2](r[c[0]],r):esc(r[c[0]])}</td>`).join("")+"</tr>").join("")+"</tbody>";\n  el.innerHTML=head+body;\n}\nfunction badgeStatus(v){\n  const cls=(v==="online"||v==="up")?"up":(v==="offline"||v==="down")?"down":"warn";\n  return `<span class="badge ${cls}">${esc(v)}</span>`;\n}\nfunction updateSelectors(){\n  const dsel=document.getElementById("sel-device");\n  const isel=document.getElementById("sel-interface");\n  const oldD=dsel.value, oldI=isel.value;\n  const devices=[...new Set(state.interfaces.map(x=>x.device))].sort();\n  dsel.innerHTML=devices.map(d=>`<option>${esc(d)}</option>`).join("");\n  if(devices.includes(oldD)) dsel.value=oldD;\n  const selected=dsel.value;\n  const ifaces=state.interfaces.filter(x=>x.device===selected).map(x=>x.interface).sort();\n  isel.innerHTML=ifaces.map(i=>`<option>${esc(i)}</option>`).join("");\n  if(ifaces.includes(oldI)) isel.value=oldI;\n}\nasync function loadState(){\n  const r=await fetch("/api/state");\n  state=await r.json();\n\n  const online=state.devices.filter(x=>x.status==="online").length;\n  const up=state.interfaces.filter(x=>x.oper_status==="up").length;\n  const maxUtil=Math.max(0,...state.latest.map(x=>Number(x.utilization_pct||0)));\n  const activeAlerts=state.alerts.filter(x=>x.status==="detected").length;\n\n  document.getElementById("c-devices").textContent=`${online}/${state.devices.length||3}`;\n  document.getElementById("c-ifaces").textContent=up;\n  document.getElementById("c-util").textContent=maxUtil.toFixed(4)+"%";\n  document.getElementById("c-alerts").textContent=activeAlerts;\n\n  table(document.getElementById("tbl-devices"),[\n    ["device","Dispositivo"],["hostname","Hostname"],["vendor","Vendor"],["host","IP"],\n    ["model","Modelo"],["version","Versión"],["status","Estado",(v)=>badgeStatus(v)],\n    ["last_seen","Última lectura",(v)=>esc(fmtTime(v))]\n  ],state.devices);\n\n  const current=state.latest.map(x=>({...x,\n    traffic_fmt:fmtRate(x.traffic_bps),\n    util_fmt:Number(x.utilization_pct||0).toFixed(4)+"%"\n  }));\n  table(document.getElementById("tbl-current"),[\n    ["device","Dispositivo"],["hostname","Hostname"],["interface","Interfaz"],\n    ["traffic_fmt","Tráfico"],["util_fmt","Utilización"],["rx_errors","RX errors"],\n    ["tx_errors","TX errors"],["threshold_exceeded",">70%",(v)=>v?\'<span class="badge down">SÍ</span>\':\'<span class="badge up">No</span>\']\n  ],current);\n\n  const ifrows=state.interfaces.map(x=>({...x,\n    speed_fmt:fmtRate(x.speed_bps),\n    rx_fmt:fmtBytes(x.rx_bytes),tx_fmt:fmtBytes(x.tx_bytes)\n  }));\n  table(document.getElementById("tbl-interfaces"),[\n    ["device","Dispositivo"],["interface","Interfaz"],["admin_status","Admin",(v)=>badgeStatus(v)],\n    ["oper_status","Oper",(v)=>badgeStatus(v)],["speed_fmt","Velocidad"],["phys_address","MAC"],\n    ["ip_address","IP"],["rx_fmt","RX"],["tx_fmt","TX"],["rx_errors","RX errors"],["tx_errors","TX errors"]\n  ],ifrows);\n\n  const alertRows=state.alerts.map(x=>({...x,\n    opened_fmt:fmtTime(x.opened_at),\n    util_fmt:Number(x.utilization_pct||0).toFixed(4)+"%",\n    threshold_fmt:Number(x.threshold_pct||0).toFixed(0)+"%"\n  }));\n  table(document.getElementById("tbl-alerts"),[\n    ["opened_fmt","Inicio"],["device","Dispositivo"],["interface","Interfaz"],\n    ["util_fmt","Utilización"],["threshold_fmt","Umbral"],["status","Estado"],\n    ["action","Acción"]\n  ],alertRows);\n\n  updateSelectors();\n  await loadHistory();\n}\nfunction drawLine(canvas, values, maxFixed=null, threshold=null){\n  const ctx=canvas.getContext("2d");\n  const W=canvas.width,H=canvas.height,padL=60,padR=20,padT=20,padB=35;\n  ctx.clearRect(0,0,W,H);\n  ctx.fillStyle="#0c1424";ctx.fillRect(0,0,W,H);\n  const maxVal=maxFixed ?? Math.max(1,...values);\n  ctx.strokeStyle="#26334f";ctx.lineWidth=1;\n  ctx.fillStyle="#93a4c7";ctx.font="12px Segoe UI";\n  for(let i=0;i<=5;i++){\n    const y=padT+(H-padT-padB)*i/5;\n    const val=maxVal*(1-i/5);\n    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();\n    ctx.fillText(val.toFixed(maxFixed?0:2),5,y+4);\n  }\n  if(threshold!==null){\n    const y=padT+(H-padT-padB)*(1-threshold/maxVal);\n    ctx.strokeStyle="#f39c12";ctx.setLineDash([7,6]);\n    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();\n    ctx.setLineDash([]);\n  }\n  if(values.length<1)return;\n  ctx.strokeStyle="#5aa9ff";ctx.lineWidth=3;ctx.beginPath();\n  values.forEach((v,i)=>{\n    const x=padL+(W-padL-padR)*(values.length===1?.5:i/(values.length-1));\n    const y=padT+(H-padT-padB)*(1-Math.min(1,Math.max(0,v/maxVal)));\n    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);\n  });\n  ctx.stroke();\n}\nasync function loadHistory(){\n  const d=document.getElementById("sel-device").value;\n  const i=document.getElementById("sel-interface").value;\n  if(!d||!i)return;\n  const r=await fetch(`/api/history?device=${encodeURIComponent(d)}&interface=${encodeURIComponent(i)}`);\n  const h=await r.json();\n  drawLine(document.getElementById("chart-util"),h.map(x=>Number(x.utilization_pct||0)),100,state?.threshold||70);\n  drawLine(document.getElementById("chart-traffic"),h.map(x=>Number(x.traffic_bps||0)));\n  const last=h[h.length-1];\n  document.getElementById("tele-meta").textContent=last\n    ? `Muestras: ${h.length} · Utilización actual: ${Number(last.utilization_pct||0).toFixed(4)}% · Tráfico actual: ${fmtRate(last.traffic_bps)}`\n    : "Todavía no hay suficientes muestras.";\n}\ndocument.querySelectorAll(".tabbtn").forEach(b=>{\n  b.onclick=()=>{\n    document.querySelectorAll(".tabbtn").forEach(x=>x.classList.remove("active"));\n    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));\n    b.classList.add("active");document.getElementById(b.dataset.tab).classList.add("active");\n  }\n});\ndocument.getElementById("sel-device").onchange=()=>{updateSelectors();loadHistory()};\ndocument.getElementById("sel-interface").onchange=loadHistory;\nloadState();\nsetInterval(loadState,5000);\n</script>\n</body>\n</html>\n'

@app.get("/")
def index():
    if not DB_PATH.exists():
        return Response(
            f"<h2>No se encontró la base de datos</h2><p>{DB_PATH}</p>",
            status=500,
            content_type="text/html; charset=utf-8",
        )
    return Response(HTML, content_type="text/html; charset=utf-8")

if __name__ == "__main__":
    print(f"[INFO] SQLite: {DB_PATH}")
    print("[INFO] Dashboard: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
