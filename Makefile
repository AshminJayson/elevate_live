.PHONY: up down test

up:
	uv run python -m bitforge.run

down:
	@SESSION="$${BITFORGE_TMUX_SESSION:-$$(grep -E '^BITFORGE_TMUX_SESSION=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')}"; \
	SESSION="$${SESSION:-class}"; \
	pkill -f "bitforge.run" || true; \
	pkill -f "bitforge.broadcaster" || true; \
	pkill -f "bitforge.serve" || true; \
	pkill -f "ttyd -p 7681" || true; \
	pkill -f "ngrok http" || true; \
	pkill -f "cloudflared tunnel" || true; \
	echo "killing tmux session: $$SESSION"; \
	tmux kill-session -t "$$SESSION" || true

test:
	uv run pytest -v
