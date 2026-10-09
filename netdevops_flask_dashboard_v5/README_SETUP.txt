Ubica esta carpeta en C:\lab-agent\netdevops_flask_dashboard_v3\nMantén el collector actual corriendo.\nEjecuta: python .\netdevops_flask_dashboard_v3\app.py\nAbre http://127.0.0.1:5000\n\nNuevas funciones: despliegues Ansible, verificación, logs, auditoría y mitigación dry-run.\nLas escrituras reales de red permanecen deshabilitadas.\n
RUNNER FIX:
- app.py invokes ansible_runner.sh directly through WSL.
- No bash -lc quoting is used.
- Vault password is passed by stdin.
- Temporary Vault file lives only in Linux /tmp and is deleted automatically.


V3 SERVIDORES
- Nueva pestaña "Servidores".
- Estado de WEB/FTP/MAIL/DNS desde el dashboard.
- Verificación individual mediante verify.yml.
- Últimos logs mediante journalctl ejecutado con Ansible.
- Reinicio controlado mediante Ansible.
- Todas las acciones quedan en automation_runs con operador, hora, resultado y log.
- Acciones limitadas por backend a web/ftp/mail/dns y logs/restart.

SERVER RUNNER FIX
- Evita Ansible become en acciones ad-hoc del dashboard.
- Usa sudo -n dentro del comando remoto aprovechando NOPASSWD ya configurado.
- Corrige timeout waiting for privilege escalation prompt al ver logs/reiniciar.


V4 - MITIGACION Y RECUPERACION
==============================

HOY / DESARROLLO:
- Dejar NETDEVOPS_ENABLE_LIVE_WRITES sin definir o en 0.
- Autorizar un puerto en la pestaña Mitigacion.
- Usar "Probar logica con 80% simulado".
- Debe crearse un incidente DRY_RUN_MITIGATED.
- Recuperar desde el boton "Recuperar".
- Debe pasar a RECOVERED y quedar auditado.
- El watcher revisa cada 5 segundos solamente el puerto armado.

PRESENTACION / ESCRITURA REAL:
NO habilitar hasta que el profesor confirme fisicamente el puerto de prueba.

Se requieren TRES coincidencias:
1) NETDEVOPS_ENABLE_LIVE_WRITES=1
2) NETDEVOPS_LIVE_DEVICE=<cisco autorizado>
3) NETDEVOPS_LIVE_INTERFACE=<interfaz autorizada>
4) El mismo device/interface debe estar ARMADO en el dashboard.

Credenciales:
El backend busca NETDEVOPS_CISCO_USERNAME y NETDEVOPS_CISCO_PASSWORD
en %USERPROFILE%\.lab-agent\secrets.env o variables de entorno.
No incluir credenciales en GitHub.

La accion real usa NETCONF/ietf-interfaces:
- enabled=false para mitigacion
- enabled=true para recuperacion

NO probar la escritura real sobre una troncal o puerto no autorizado.

V5 SYSTEM INFO
- Resumen muestra version IOS-XE, uptime y boot time de Cisco.
- Requiere collector.py actualizado y labctl snapshot con system info.
