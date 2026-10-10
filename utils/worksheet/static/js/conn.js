// The WebSocket to the worksheet server (and through it, the kernel).

export class Conn {
  /**
   * @param {object} opts
   * @param {string} opts.session   id kept for the life of the tab, so a
   *                                reload finds its kernel (and results) again
   * @param {() => string} opts.path  the sheet's path, for the kernel's folder
   * @param {(msg: object) => void} opts.onMessage
   * @param {(online: boolean) => void} opts.onState
   */
  constructor({ session, path, onMessage, onState }) {
    this.session = session;
    this.path = path;
    this.onMessage = onMessage;
    this.onState = onState;
    this.socket = null;
    this.seq = 0;
    this.retry = 0;
    this.closed = false;
  }

  get online() {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  connect() {
    if (this.closed) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ session: this.session, path: this.path() || "" });
    const socket = new WebSocket(`${scheme}://${location.host}/ws?${query}`);
    this.socket = socket;
    socket.onopen = () => {
      this.retry = 0;
      this.onState(true);
    };
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (error) { return; }
      this.onMessage(message);
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.onState(false, event.code);
      if (this.closed || event.code === 4403 || event.code === 4000) return;
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 250 * 2 ** this.retry);
    };
  }

  /** Send a request; returns its sequence number, or 0 when offline. */
  send(message) {
    if (!this.online) return 0;
    this.seq += 1;
    this.socket.send(JSON.stringify({ ...message, seq: this.seq }));
    return this.seq;
  }

  /** Connect afresh (the user asks to, after the server refused or dropped this tab). */
  reopen() {
    const socket = this.socket;
    this.socket = null;
    if (socket) socket.close();
    this.connect();
  }
}
