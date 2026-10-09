#!/usr/bin/env bash
set -euo pipefail

PLAYBOOK="${1:?playbook required}"
LIMIT="${2:-all}"

cd /mnt/c/lab-agent/ansible
export ANSIBLE_CONFIG=/mnt/c/lab-agent/ansible/ansible.cfg

# Usar la misma version de Ansible que ya validamos.
ANSIBLE_PLAYBOOK="/home/alvar/.venvs/netdevops-ansible/bin/ansible-playbook"

if [[ ! -x "$ANSIBLE_PLAYBOOK" ]]; then
    ANSIBLE_PLAYBOOK="/usr/bin/ansible-playbook"
fi

umask 077
VAULT_FILE="$(mktemp /tmp/netdevops_vault_XXXXXX)"

cleanup() {
    rm -f "$VAULT_FILE"
}
trap cleanup EXIT

IFS= read -r VAULT_PASS
printf '%s\n' "$VAULT_PASS" > "$VAULT_FILE"
unset VAULT_PASS

CMD=(
    "$ANSIBLE_PLAYBOOK"
    "$PLAYBOOK"
    --vault-password-file "$VAULT_FILE"
)

# Workaround validado para los despliegues:
# el become nativo de Ansible se bloquea en este entorno,
# pero sudo NOPASSWD funciona correctamente.
if [[ "$PLAYBOOK" == "site.yml" ]]; then
    CMD+=(
        -e ansible_become=false
        -e ansible_python_interpreter=/usr/local/bin/ansible-python-root
    )
fi

if [[ -n "$LIMIT" && "$LIMIT" != "all" ]]; then
    CMD+=(--limit "$LIMIT")
fi

"${CMD[@]}"
