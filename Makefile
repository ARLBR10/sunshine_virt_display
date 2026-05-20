REPO         := $(CURDIR)
DEV_SERVICE  := sunshineVD-dev
DEV_SVC_FILE := /etc/systemd/system/$(DEV_SERVICE).service

.PHONY: dev-install dev-uninstall dev-start dev-stop dev-restart dev-logs dev-status install

# ── Development ──────────────────────────────────────────────────────────────

dev-install:
	@printf '[Unit]\n\
Description=Sunshine Virtual Display Daemon (dev)\n\
After=display-manager.service dbus.service\n\
Requires=dbus.service\n\
\n\
[Service]\n\
Type=simple\n\
User=root\n\
Group=root\n\
ExecStart=/usr/bin/python3 $(REPO)/src/daemon/daemon.py\n\
Restart=no\n\
TimeoutStopSec=10\n\
\n\
[Install]\n\
WantedBy=multi-user.target\n' \
	| sudo tee $(DEV_SVC_FILE) > /dev/null
	sudo systemctl daemon-reload
	@echo "Dev service installed at $(DEV_SVC_FILE)"
	@echo "Run 'make dev-start' to start, 'make dev-logs' to follow output."

dev-uninstall: dev-stop
	sudo rm -f $(DEV_SVC_FILE)
	sudo systemctl daemon-reload
	@echo "Dev service removed."

dev-start:
	sudo systemctl start $(DEV_SERVICE)

dev-stop:
	-sudo systemctl stop $(DEV_SERVICE) 2>/dev/null

dev-restart:
	sudo systemctl restart $(DEV_SERVICE)

dev-logs:
	journalctl -u $(DEV_SERVICE) -f --output=short

dev-status:
	systemctl status $(DEV_SERVICE) --no-pager || true

# ── Production ────────────────────────────────────────────────────────────────

install:
	sudo ./install.sh
