FASE 3 - SQLITE + STREAMLIT

Ubicación recomendada:
C:\lab-agent\netdevops_dashboard_phase3

1) Extrae/copia esta carpeta completa dentro de C:\lab-agent

2) Desde PowerShell:
   cd C:\lab-agent
   .\.venv\Scripts\Activate.ps1
   pip install -r .\netdevops_dashboard_phase3\requirements-dashboard.txt

3) Prueba una primera recolección:
   python -m netdevops_dashboard_phase3.app.collector --once

4) Espera unos segundos y repite:
   python -m netdevops_dashboard_phase3.app.collector --once

   La muestra previa queda almacenada en SQLite, por lo que la segunda ejecución
   ya puede calcular deltas y utilización aunque sea otro proceso Python.

5) Inicia el collector continuo:
   python -m netdevops_dashboard_phase3.app.collector --interval 10

6) Abre OTRA terminal PowerShell:
   cd C:\lab-agent
   .\.venv\Scripts\Activate.ps1
   streamlit run .\netdevops_dashboard_phase3\app\dashboard.py

También puedes usar:
   .\netdevops_dashboard_phase3\run_collector.cmd
   .\netdevops_dashboard_phase3\run_dashboard.cmd

IMPORTANTE
- labctl.cmd sigue siendo el único backend que habla con Cisco/FortiGate.
- Los dispositivos compartidos permanecen read-only.
- >70% genera READ_ONLY_ALERT; NO ejecuta shutdown.
- La mitigación real se añadirá luego sobre infraestructura administrada por el grupo.
- El intervalo solicitado es objetivo. Los timestamps reales determinan Δt, así que
  una consulta NETCONF que tarde varios segundos no rompe el cálculo.
