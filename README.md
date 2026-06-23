# LiveClass

View-only live broadcast of code, file tree, and terminal for teaching.

## Prerequisites

    brew install ttyd tmux ngrok
    uv pip install -e ".[dev]"

## Run

    export LIVECLASS_TOKEN=some-shared-secret
    export NGROK_DOMAIN=your-reserved.ngrok-free.dev   # optional
    make up

Then attach your editor's terminal to the shared tmux session:

    tmux attach -t class

Run uvicorn / curl / tests inside that session — students see it live.
Edit files under `./lesson/`; saves broadcast to students.

Students open the ngrok URL: explorer + live code + read-only terminal,
all selectable/copyable.

## Config

Edit `liveclass.toml` any time (hot-reloaded): `ignore` list, terminal
`cols`/`rows`, `tmux_session`, `title`. Keep real secrets out of `./lesson/`
— everything visible there is broadcast verbatim.

## Test

    make test
