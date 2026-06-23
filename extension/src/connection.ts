import WebSocket from "ws";

/** Connection state for the status bar / commands. */
export type ConnState = "connecting" | "live" | "reconnecting" | "stopped";

/**
 * Persistent teacher WebSocket with exponential-backoff reconnect.
 *
 * Mirrors the broadcaster's reconnect loop and the student page's backoff curve
 * (1s doubling to 30s). Sends are best-effort: if the socket is not open the
 * message is dropped, which is safe because the caller always resends the full
 * current buffer on the next change.
 */
export class TeacherConnection {
  private ws: WebSocket | null = null;
  private timer: NodeJS.Timeout | null = null;
  private delay = 1000;
  private stopped = false;

  /**
   * @param url full teacher URL, e.g. "ws://127.0.0.1:8000/ws/teacher?token=..."
   * @param onState callback invoked whenever the connection state changes
   * @param onAuthRejected callback invoked when the hub closes with 1008 (bad
   *   token); reconnecting would just loop, so the caller re-prompts instead
   */
  constructor(
    private readonly url: string,
    private readonly onState: (state: ConnState) => void,
    private readonly onAuthRejected: () => void
  ) {}

  /** Open the socket (idempotent); schedules a retry on close/error. */
  connect(): void {
    this.stopped = false;
    this.onState("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.on("open", () => {
      this.delay = 1000;
      this.onState("live");
    });
    ws.on("close", (code: number) => {
      if (code === 1008) {
        // token rejected by the hub: stop looping and let the caller re-prompt
        this.stopped = true;
        this.ws = null;
        this.onState("stopped");
        this.onAuthRejected();
        return;
      }
      this.scheduleReconnect();
    });
    ws.on("error", () => {
      // surfaced via the close handler that follows; avoid crashing the host
      try {
        ws.close();
      } catch {
        /* already closing */
      }
    });
  }

  /** Send a wire message if the socket is open; drop it otherwise. */
  send(message: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  /** Close the socket and stop reconnecting. */
  dispose(): void {
    this.stopped = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
    this.onState("stopped");
  }

  /** Schedule a reconnect with exponential backoff unless disposed. */
  private scheduleReconnect(): void {
    this.ws = null;
    if (this.stopped) {
      return;
    }
    this.onState("reconnecting");
    this.timer = setTimeout(() => this.connect(), this.delay);
    this.delay = Math.min(this.delay * 2, 30000);
  }
}
