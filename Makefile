.PHONY: up down test

up:
	uv run python -m liveclass.run

down:
	- pkill -f "liveclass.broadcaster" || true
	- pkill -f "uvicorn liveclass.server" || true
	- pkill -f "ttyd -p 7681" || true
	- pkill -f "ngrok http" || true
	- tmux kill-session -t class || true

test:
	uv run pytest -v
