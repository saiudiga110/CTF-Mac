# ─────────────────────────────────────────────────────────────────────────────
# Lloyds CTF — Makefile entry point (Mac / Linux / WSL2)
# Usage:
#   make          → install Docker if needed, auto-detect IP, build & start
#   make start    → same as above
#   make stop     → stop all services
#   make restart  → restart with fresh IP detection
#   make logs     → tail all service logs
#   make status   → show running containers
#   make clean    → stop and remove all CTF containers/networks/volumes
# ─────────────────────────────────────────────────────────────────────────────
SHELL := /bin/bash
.DEFAULT_GOAL := start

.PHONY: start stop restart logs status clean prod

start:
	@bash start.sh

stop:
	@bash start.sh --down

restart:
	@bash start.sh --restart

logs:
	@docker compose logs -f

prod:
	@bash start.sh --production

status:
	@docker compose ps

clean:
	@echo "Stopping and removing all CTF containers, networks and volumes..."
	@docker compose down -v --remove-orphans
	@docker ps -aq --filter "label=ctfd_target_user" | xargs -r docker rm -f
	@docker network ls --filter "name=ctfd-net-" -q | xargs -r docker network rm
	@echo "Clean complete."
