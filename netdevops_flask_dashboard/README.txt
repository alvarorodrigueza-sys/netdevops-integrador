NETDEVOPS FLASK DASHBOARD

Este dashboard reemplaza Streamlit porque Windows Application Control está
bloqueando extensiones compiladas de pandas usadas internamente por Streamlit.

No modifica labctl, collector ni SQLite.

INSTALACIÓN
1. Copiar la carpeta netdevops_flask_dashboard dentro de C:\lab-agent

2. PowerShell:
   cd C:\lab-agent
   .\.venv\Scripts\Activate.ps1
   pip install -r .\netdevops_flask_dashboard\requirements.txt

3. Mantener corriendo el collector en otra terminal:
   python -m netdevops_dashboard_phase3.app.collector --interval 10

4. Iniciar dashboard:
   python .\netdevops_flask_dashboard\app.py

5. Abrir:
   http://127.0.0.1:5000

El dashboard consulta:
C:\lab-agent\netdevops_dashboard_phase3\data\netdevops.db

Los equipos cisco01, cisco02 y fortigate01 siguen siendo read-only.
