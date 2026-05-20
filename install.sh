#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=/opt/sunshine-vd
SERVICE_DEST=/etc/systemd/system/sunshineVD.service

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo ./install.sh"; exit 1; }

echo "==> Installing jeepney..."
python3 -m pip install --quiet jeepney

echo "==> Copying project to $INSTALL_DIR..."
install -d "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.coverage' \
    --exclude='custom_edid.bin' \
    --exclude='virt_display.state' \
    . "$INSTALL_DIR/"

echo "==> Installing systemd service..."
install -m 644 src/daemon/sunshineVD.service "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable --now sunshineVD

echo ""
echo "Done. Status:"
systemctl status sunshineVD --no-pager || true
