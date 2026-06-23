"""Logging configuration: a clean host console plus a detailed .log file.

The supervisor runs several noisy children (uvicorn, ttyd, ngrok, the
broadcaster). A host running a session does not want that firehose in their
terminal; they want a quiet console and a file they can scroll back through.

This module owns the single logging contract that splits those two audiences:

  - bitforge.console  -> the host's terminal AND the file. Used only for the
        periodic heartbeat (current viewers online). This is the only thing
        allowed to reach the console, which is what keeps it clean.
  - bitforge.events   -> the file only. Per-connection detail (a viewer joined
        or left, a terminal reader attached, a dropped socket).
  - uvicorn / uvicorn.error / uvicorn.access -> the file only. uvicorn's startup
        banner and per-request access lines are useful history but pure console
        noise during a session.
  - root              -> the file, at WARNING, so nothing unexpected is lost.

uvicorn applies this same dict (passed as its log_config), so its own logging is
configured by exactly this routing rather than its defaults. See bitforge.serve.
"""

import logging


def build_log_config(log_path):
    """Build a logging.config.dictConfig dict routing console vs file output.

    Algorithm:
        1. Define two formatters: a bare 'clean' one for the console (message
           only, since heartbeat lines carry their own timestamp) and a
           'detailed' one for the file (UTC-naive local time, level, logger,
           message) so the file is greppable history.
        2. Define a 'console' StreamHandler (stdout) and a 'file' FileHandler
           appending to log_path.
        3. Wire each logger to the handler set described in the module docstring,
           all with propagate=False so a record lands in exactly one place and is
           never doubled via the root logger.

    Args:
        log_path (str): filesystem path for the detailed append-mode log file.

    Returns:
        dict: a dictConfig-compatible mapping with keys 'version', 'formatters',
            'handlers', 'loggers', 'root'. Handler 'file' targets log_path.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "clean": {"format": "%(message)s"},
            "detailed": {
                "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "clean",
                "level": "INFO",
            },
            "file": {
                "class": "logging.FileHandler",
                "filename": log_path,
                "mode": "a",
                "encoding": "utf-8",
                "formatter": "detailed",
                "level": "INFO",
            },
        },
        "loggers": {
            "bitforge.console": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
            "bitforge.events": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["file"], "level": "WARNING"},
    }


if __name__ == "__main__":
    import json

    print(json.dumps(build_log_config("bitforge.log"), indent=2))
