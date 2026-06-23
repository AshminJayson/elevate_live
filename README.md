# LiveClass

View-only live broadcast of code, file tree, and terminal for teaching.

## Prerequisites

    brew install ttyd tmux ngrok
    uv pip install -e ".[dev]"

## Run

    export LIVECLASS_TOKEN=some-shared-secret
    export NGROK_DOMAIN=your-reserved.ngrok-free.dev   # optional
    export LIVECLASS_CONFIG=liveclass.toml             # optional; overrides config file path
    make up

Then attach your editor's terminal to the shared tmux session:

    tmux attach -t class

Run uvicorn / curl / tests inside that session — students see it live.
Edit files under `./lesson/`; saves broadcast to students.

Students open the ngrok URL: explorer + live code + read-only terminal,
all selectable/copyable.

## Access control

The ngrok URL is the only access control. Every student route (`/`,
`/ws/student`, `/file`, `/terminal`) is unauthenticated by design — anyone
with the URL can view the broadcast. `LIVECLASS_TOKEN` gates only the
teacher/broadcaster connection (`/ws/teacher`), not student access. Share the
URL only with your class, and treat it as a secret: keep real secrets out of
`./lesson/` (everything there is broadcast verbatim).

## Config

Edit `liveclass.toml` any time (hot-reloaded): `ignore` list, terminal
`cols`/`rows`, `tmux_session`, `title`. Keep real secrets out of `./lesson/`
— everything visible there is broadcast verbatim.

## Test

    make test
