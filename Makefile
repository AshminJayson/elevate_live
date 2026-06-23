.PHONY: up down test

up:
	uv run python -m liveclass.run

down:
	@SESSION="$${LIVECLASS_TMUX_SESSION:-$$(grep -E '^LIVECLASS_TMUX_SESSION=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')}"; \
	SESSION="$${SESSION:-class}"; \
	pkill -f "liveclass.broadcaster" || true; \
	pkill -f "uvicorn liveclass.server" || true; \
	pkill -f "ttyd -p 7681" || true; \
	pkill -f "ngrok http" || true; \
	echo "killing tmux session: $$SESSION"; \
	tmux kill-session -t "$$SESSION" || true

test:
	uv run pytest -v
