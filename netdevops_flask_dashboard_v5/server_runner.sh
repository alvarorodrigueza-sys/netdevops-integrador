#!/usr/bin/env bash
set -euo pipefail

GROUP="${1:?group required}"
OPERATION="${2:?operation required}"

cd /mnt/c/lab-agent/ansible
export ANSIBLE_CONFIG=/mnt/c/lab-agent/ansible/ansible.cfg

case "$GROUP" in
  web)
    UNIT="nginx"
    LOG_CMD="sudo -n journalctl -u nginx -n 120 --no-pager"
    RESTART_CMD="sudo -n systemctl restart nginx && sudo -n systemctl is-active nginx"
    ;;
  ftp)
    UNIT="vsftpd"
    LOG_CMD="sudo -n journalctl -u vsftpd -n 120 --no-pager"
    RESTART_CMD="sudo -n systemctl restart vsftpd && sudo -n systemctl is-active vsftpd"
    ;;
  mail)
    UNIT="postfix+dovecot"
    LOG_CMD="sudo -n journalctl -u postfix -u dovecot -n 160 --no-pager"
    RESTART_CMD="sudo -n systemctl restart postfix dovecot && sudo -n systemctl is-active postfix && sudo -n systemctl is-active dovecot"
    ;;
  dns)
    UNIT="bind9"
    LOG_CMD="sudo -n journalctl -u bind9 -n 120 --no-pager"
    RESTART_CMD="sudo -n systemctl restart bind9 && sudo -n systemctl is-active bind9"
    ;;
  *)
    echo "Grupo no permitido: $GROUP"
    exit 2
    ;;
esac

case "$OPERATION" in
  logs|restart) ;;
  *)
    echo "Operación no permitida: $OPERATION"
    exit 2
    ;;
esac

umask 077
VAULT_FILE="$(mktemp /tmp/netdevops_vault_XXXXXX)"
cleanup() { rm -f "$VAULT_FILE"; }
trap cleanup EXIT

IFS= read -r VAULT_PASS
printf '%s\n' "$VAULT_PASS" > "$VAULT_FILE"
unset VAULT_PASS

if [[ "$OPERATION" == "logs" ]]; then
  echo "=== $GROUP / $UNIT : últimos logs ==="
  /usr/bin/ansible "$GROUP" \
    --vault-password-file "$VAULT_FILE" \
    -e ansible_become=false \
    -m shell -a "$LOG_CMD"
else
  echo "=== $GROUP / $UNIT : reinicio controlado ==="
  /usr/bin/ansible "$GROUP" \
    --vault-password-file "$VAULT_FILE" \
    -e ansible_become=false \
    -m shell -a "$RESTART_CMD"
fi
