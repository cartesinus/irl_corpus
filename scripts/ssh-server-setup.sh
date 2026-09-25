#!/usr/bin/env bash
#   sudo CORPUS_ROOT=/mnt/secure_hdd/twitch ./scripts/ssh-server-setup.sh
set -euo pipefail

CORPUS_ROOT="${CORPUS_ROOT:?Set CORPUS_ROOT to your [storage] root before running}"
SSH_USER="${SSH_USER:-irl}"
SSH_HOME="${SSH_HOME:-/home/${SSH_USER}}"
SSHD_CONFIG="${SSHD_CONFIG:-/etc/ssh/sshd_config}"

if [ ! -d "$CORPUS_ROOT" ]; then
    echo "CORPUS_ROOT '$CORPUS_ROOT' does not exist." >&2
    exit 1
fi

apt-get install -y openssh-server
systemctl enable --now ssh

if id "$SSH_USER" >/dev/null 2>&1; then
    echo "Skipping account creation for '${SSH_USER}'."
else
    adduser --system --group --shell /usr/sbin/nologin --home "$SSH_HOME" "$SSH_USER"
    echo "Created account '${SSH_USER}'."
fi

chown -R "${SSH_USER}:${SSH_USER}" "$CORPUS_ROOT"

install -d -m 700 -o "$SSH_USER" -g "$SSH_USER" "${SSH_HOME}/.ssh"
install -m 600 -o "$SSH_USER" -g "$SSH_USER" /dev/null "${SSH_HOME}/.ssh/authorized_keys.new"
if [ -f "${SSH_HOME}/.ssh/authorized_keys" ]; then
    rm -f "${SSH_HOME}/.ssh/authorized_keys.new"
else
    mv "${SSH_HOME}/.ssh/authorized_keys.new" "${SSH_HOME}/.ssh/authorized_keys"
fi

if grep -q "^ListenAddress" "$SSHD_CONFIG"; then
    echo "ListenAddress configuration already present."
elif ! command -v tailscale >/dev/null; then
    echo "Tailscale not found, skipping ListenAddress restriction."
else
    printf 'ListenAddress 127.0.0.1\nListenAddress %s\n' "$(tailscale ip -4)" \
        >> "$SSHD_CONFIG"
    echo "Restricted sshd to localhost and the tailnet address."
fi

if grep -q "^Match User ${SSH_USER}\$" "$SSHD_CONFIG"; then
    echo "sshd Match block for '${SSH_USER}' already present."
else
    cat >> "$SSHD_CONFIG" <<EOF
Match User ${SSH_USER}
    ForceCommand internal-sftp
    PermitTTY no
    AllowTcpForwarding no
EOF
    echo "Restricted '${SSH_USER}' to file transfer."
fi

sshd -t
systemctl restart ssh

echo
echo "Done. Add an annotator's public key with:"
echo "  printf 'from=\"100.64.0.0/10\",restrict %s\\n' '<public key>' \\"
echo "      | sudo -u ${SSH_USER} tee -a ${SSH_HOME}/.ssh/authorized_keys"
