.PHONY: setup seed dev test build openapi audio-sim clean help
help:
	@echo "setup seed dev test build openapi audio-sim clean"

setup:
	cd backend && uv sync
	cd student-pwa && npm ci
	cd prof-console && npm ci

seed:
	cd backend && uv run python scripts/seed.py

dev:
	@trap 'kill 0' INT TERM; \
	(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd student-pwa && npm run dev) & \
	(cd prof-console && npm run dev) & \
	wait

test:
	cd backend && uv run pytest -q

build:
	cd student-pwa && npm run build
	cd prof-console && npm run build

openapi:
	cd backend && uv run python scripts/export_openapi.py

audio-sim:
	cd bench && npx esbuild audio_sim.ts --bundle --platform=node --format=cjs | node

clean:
	rm -rf student-pwa/dist prof-console/dist backend/.pytest_cache backend/gatekeeper.db*
