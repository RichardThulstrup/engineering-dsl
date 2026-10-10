"""The worksheet's local web server.

    python -m utils.worksheet [notebook.ipynb | folder]

Serves the front end, reads and writes notebooks below one folder, and
relays between each browser tab and its kernel over a WebSocket.

The server runs code, so it listens on the loopback interface only and
every request must carry the session token: the URL printed at start-up
contains it once and trades it for a cookie.  ``Host`` and ``Origin``
are checked as well, so a web page open elsewhere in the same browser
cannot reach it.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlsplit

import tornado.httpserver
import tornado.web
import tornado.websocket

from . import document
from .session import KernelSession

__all__ = ["main", "make_app", "Config"]

STATIC = Path(__file__).with_name("static")
DEFAULT_PORT = 8791
SESSION_LINGER = 45.0        # seconds a kernel outlives its tab (covers a reload)
SPARE_LINGER = 120.0         # seconds the kernel started ahead waits for a first tab


class Config:
    def __init__(self, root: Path, port: int, token: str, palette: bool = False):
        self.root = root.resolve()
        self.port = port
        self.token = token
        self.palette = palette
        self.sessions: dict[str, _Session] = {}
        # Both set by ``_serve``, to have a sheet on screen and calculated
        # sooner: a kernel started before any page asks for one, and the
        # (slow) import of the notebook format done off the event loop.
        self.spare: _Session | None = None
        self.warm = None

    @property
    def cookie(self) -> str:
        return f"edsl_ws_{self.port}"        # cookies ignore ports; the name must not

    @property
    def hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}


class _Session:
    """A browser tab's kernel, kept across reconnects of its socket."""

    def __init__(self, config: Config, cwd: str):
        self.socket = None
        self.kernel = KernelSession(cwd, self.emit, palette=config.palette)
        self.closing = None

    def emit(self, message: dict):
        socket = self.socket
        if socket is None:
            return
        try:
            socket.write_message(json.dumps(message, ensure_ascii=False))
        except tornado.websocket.WebSocketClosedError:
            pass


class _Base(tornado.web.RequestHandler):
    @property
    def config(self) -> Config:
        return self.application.settings["config"]

    def prepare(self):
        config = self.config
        if self.request.host not in config.hosts:
            raise tornado.web.HTTPError(403, "unexpected Host header")
        origin = self.request.headers.get("Origin")
        if origin and urlsplit(origin).netloc not in config.hosts:
            raise tornado.web.HTTPError(403, "cross-origin request")

    def authorized(self) -> bool:
        cookie = self.get_cookie(self.config.cookie) or ""
        return hmac.compare_digest(cookie, self.config.token)

    def require_auth(self):
        if not self.authorized():
            raise tornado.web.HTTPError(403, "not signed in to this worksheet server")

    async def notebooks_ready(self):
        """Wait until notebooks can be read and written without stalling
        the event loop on an import (see ``_serve``)."""
        if self.config.warm is not None:
            await self.config.warm

    def json(self, payload, status=200):
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.finish(json.dumps(payload, ensure_ascii=False))

    def write_error(self, status_code, **kwargs):
        reason = self._reason
        exc = kwargs.get("exc_info", (None, None, None))[1]
        if isinstance(exc, tornado.web.HTTPError) and exc.log_message:
            reason = exc.log_message
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"error": reason}))


class _Index(_Base):
    def get(self):
        config = self.config
        token = self.get_argument("token", None)
        if token is not None:
            if not hmac.compare_digest(token, config.token):
                raise tornado.web.HTTPError(403, "wrong token")
            self.set_cookie(config.cookie, config.token, httponly=True, samesite="Strict")
            target = self.get_argument("open", None)
            self.redirect("/" + (f"?open={quote(target)}" if target else ""))
            return
        if not self.authorized():
            self.set_status(403)
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.finish("<!doctype html><meta charset=utf-8><title>Worksheet</title>"
                        "<body style='font:15px system-ui;margin:3em'>"
                        "<h3>Engineering DSL worksheet</h3><p>Open the address printed in "
                        "the terminal where the server was started; it carries the access "
                        "token this page needs.</p>")
            return
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-cache")
        self.finish((STATIC / "index.html").read_bytes())


class _Static(tornado.web.StaticFileHandler):
    # Said here rather than asked of the operating system: Windows keeps
    # these in its registry, where ``.mjs`` (and on some machines ``.js``)
    # reads as plain text — and a browser does not run a module of that type.
    TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
             ".json": "application/json", ".html": "text/html", ".woff2": "font/woff2"}

    def prepare(self):
        if self.request.host not in self.application.settings["config"].hosts:
            raise tornado.web.HTTPError(403)

    def get_content_type(self):
        return self.TYPES.get(Path(self.absolute_path).suffix.lower()) or super().get_content_type()

    def compute_etag(self):
        # The validator covers the type as well as the bytes.  "Not
        # modified" carries no type of its own, so a browser that once got
        # this file as plain text (from a server that asked the registry)
        # would otherwise keep it as plain text for good.
        etag = super().compute_etag()
        return etag and '%s-%s"' % (etag[:-1], self.get_content_type().rpartition("/")[2])

    def set_extra_headers(self, path):
        # Revalidate every time: an updated toolkit should not be met by
        # a stale front end.
        self.set_header("Cache-Control", "no-cache")


class _Info(_Base):
    def get(self):
        self.require_auth()
        config = self.config
        self.json({"root": str(config.root), "name": config.root.name})


class _List(_Base):
    def get(self):
        self.require_auth()
        try:
            self.json(document.listing(self.config.root, self.get_argument("dir", "")))
        except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
            raise tornado.web.HTTPError(404, str(exc))


class _Doc(_Base):
    def _path(self) -> Path:
        relative = self.get_argument("path")
        if not relative.lower().endswith(".ipynb"):
            raise tornado.web.HTTPError(400, "worksheets are .ipynb files")
        try:
            return document.resolve(self.config.root, relative)
        except PermissionError as exc:
            raise tornado.web.HTTPError(403, str(exc))

    async def get(self):
        self.require_auth()
        path = self._path()
        if not path.is_file():
            raise tornado.web.HTTPError(404, f"{path.name} was not found")
        await self.notebooks_ready()
        try:
            doc = document.load(path)
        except Exception as exc:
            raise tornado.web.HTTPError(422, f"{path.name} is not a readable notebook: {exc}")
        doc["path"] = path.relative_to(self.config.root).as_posix()
        self.json(doc)

    async def put(self):
        self.require_auth()
        path = self._path()
        try:
            body = json.loads(self.request.body.decode("utf-8"))
        except ValueError:
            raise tornado.web.HTTPError(400, "malformed request")
        if body.get("create") and path.exists() and not body.get("force"):
            self.json({"exists": True}, 409)
            return
        if not path.parent.is_dir():
            raise tornado.web.HTTPError(404, "that folder does not exist")
        await self.notebooks_ready()
        try:
            saved = document.save(path, body.get("doc") or {},
                                  expect_mtime=body.get("mtime"), force=bool(body.get("force")))
        except document.Conflict:
            self.json({"conflict": True}, 409)
            return
        except Exception as exc:
            raise tornado.web.HTTPError(500, f"could not save: {exc}")
        saved["path"] = path.relative_to(self.config.root).as_posix()
        self.json(saved)


class _Trust(_Doc):
    """The user chose to calculate a sheet: remember it as trusted."""

    def get(self):
        raise tornado.web.HTTPError(405)

    put = get

    async def post(self):
        self.require_auth()
        path = self._path()
        try:
            body = json.loads(self.request.body.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        await self.notebooks_ready()
        ok = path.is_file() and document.trust(path, expect_mtime=body.get("mtime"))
        self.json({"trusted": bool(ok)})


class _Socket(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        config = self.application.settings["config"]
        return urlsplit(origin).netloc in config.hosts and self.request.host in config.hosts

    def open(self):
        config: Config = self.application.settings["config"]
        cookie = self.get_cookie(config.cookie) or ""
        if not hmac.compare_digest(cookie, config.token):
            self.close(4403, "not signed in")
            return
        self.sid = self.get_argument("session")
        session = config.sessions.get(self.sid)
        if session is not None and session.kernel.cwd != self._cwd(config):
            # The tab now shows a sheet in another folder (saved there, or
            # opened by its address): a kernel works in its sheet's folder.
            self._retire(session)
            session = None
        if session is None:
            session = config.sessions[self.sid] = self._new_session(config)
        if session.closing is not None:
            session.closing.cancel()
            session.closing = None
        if session.socket is not None and session.socket is not self:
            session.socket.close(4000, "opened elsewhere")
        session.socket = self
        self.session = session
        status = {"op": "status", "kernel": session.kernel.state}
        if session.kernel.detail:
            status["detail"] = session.kernel.detail
        session.emit(status)

    def _new_session(self, config: Config) -> _Session:
        """A kernel for a tab that has none: the one started ahead, if it
        is still unclaimed and works in the folder this sheet lives in."""
        cwd = self._cwd(config)
        spare, config.spare = config.spare, None
        if spare is not None:
            if spare.kernel.cwd == cwd and spare.kernel.state != "dead":
                return spare
            _spawn(spare.kernel.close())
        return _Session(config, cwd)

    @staticmethod
    def _retire(session: _Session):
        """End a session whose tab has moved on."""
        if session.closing is not None:
            session.closing.cancel()
            session.closing = None
        left, session.socket = session.socket, None
        if left is not None:
            left.close(4000, "opened elsewhere")
        _spawn(session.kernel.close())

    def _cwd(self, config: Config, relative=None) -> str:
        """The folder a sheet's kernel works in: the sheet's own."""
        if relative is None:
            relative = self.get_argument("path", "")
        try:
            target = document.resolve(config.root, relative)
        except PermissionError:
            return str(config.root)
        folder = target if target.is_dir() else target.parent
        return str(folder if folder.is_dir() else config.root)

    def on_message(self, raw):
        session = getattr(self, "session", None)
        if session is None:
            return
        try:
            message = json.loads(raw)
            op = message["op"]
        except (ValueError, KeyError, TypeError):
            return
        kernel = session.kernel
        # Each request runs as its own task: ``on_message`` must return at
        # once, or "stop" would wait behind the calculation it is to stop.
        if op in ("calc", "render", "hello"):
            _spawn(kernel.request(message))
        elif op == "interrupt":
            _spawn(kernel.interrupt())
        elif op == "restart":
            path = message.get("path")
            _spawn(kernel.restart(self._cwd(self.application.settings["config"], path)
                                  if isinstance(path, str) else None))

    def on_close(self):
        session = getattr(self, "session", None)
        if session is None or session.socket is not self:
            return
        session.socket = None
        config: Config = self.application.settings["config"]
        sid = self.sid

        async def linger():
            await asyncio.sleep(SESSION_LINGER)
            if config.sessions.get(sid) is session and session.socket is None:
                del config.sessions[sid]
                await session.kernel.close()

        session.closing = asyncio.ensure_future(linger())


_background = set()


def _spawn(coro):
    task = asyncio.ensure_future(coro)
    _background.add(task)                    # the loop holds tasks only weakly
    task.add_done_callback(_background.discard)
    return task


def make_app(config: Config) -> tornado.web.Application:
    return tornado.web.Application([
        (r"/", _Index),
        (r"/api/info", _Info),
        (r"/api/list", _List),
        (r"/api/doc", _Doc),
        (r"/api/trust", _Trust),
        (r"/ws", _Socket),
        (r"/static/(.*)", _Static, {"path": str(STATIC)}),
    ], config=config, websocket_max_message_size=64 * 1024 * 1024,
        websocket_ping_interval=25,
        # Tornado would otherwise remember each file's hash for as long as
        # it runs, and answer "not modified" for a front end that was
        # updated under a running server — which then meets a new kernel.
        static_hash_cache=False)


async def _serve(config: Config, url: str, launch):
    app = make_app(config)
    server = tornado.httpserver.HTTPServer(app, max_buffer_size=128 * 1024 * 1024)
    server.listen(config.port, address="127.0.0.1")
    # Plain ASCII: a Windows console may not be able to print anything else.
    print(f"Engineering DSL worksheet:  {url}\n(folder: {config.root};  Ctrl+C to stop)",
          flush=True)
    # A kernel takes seconds to start and the notebook format a second or
    # more to import.  Both begin now, while the browser opens, instead
    # of one after the other once the page asks.
    config.warm = asyncio.get_running_loop().run_in_executor(None, document.warm_up)
    spare = config.spare = _Session(config, str(config.root))
    _spawn(spare.kernel.start())
    _spawn(_expire_spare(config, spare))
    if launch is not None:
        launch(url)
    try:
        while True:
            await asyncio.sleep(0.5)         # keeps Ctrl+C responsive on Windows
    finally:
        server.stop()
        sessions = list(config.sessions.values())
        if config.spare is not None:
            sessions.append(config.spare)
        for session in sessions:
            await session.kernel.close()


async def _expire_spare(config: Config, spare: _Session):
    """Nobody came for the kernel started ahead: do not keep it running."""
    await asyncio.sleep(SPARE_LINGER)
    if config.spare is spare:
        config.spare = None
        await spare.kernel.close()


def _free_port(preferred: int) -> int:
    import socket
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"no free port between {preferred} and {preferred + 39}")


def _app_window(url: str) -> bool:
    """Open ``url`` in a chromeless browser window, if Edge or Chrome is found.

    A browser installed in the usual place comes first.  One that is merely
    on the PATH is the last resort: on a developer's machine that is often
    a build kept for automated tests (Chrome for Testing), years old.
    """
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LocalAppData")):
        if base:
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    candidates += [shutil.which(n) for n in ("msedge", "chrome", "google-chrome", "chromium")]
    for exe in candidates:
        if exe and os.path.isfile(exe):
            subprocess.Popen([exe, f"--app={url}"])
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m utils.worksheet",
        description="Mathcad-style worksheet front end for the Engineering DSL.")
    parser.add_argument("path", nargs="?", help="a notebook to open, or the folder to work in")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to listen on (default {DEFAULT_PORT}; the next free one is used)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--app", action="store_true",
                        help="open in a chromeless Edge/Chrome window instead of a tab")
    parser.add_argument("--palette", action="store_true",
                        help="also start the Windows symbol palette, as a notebook does "
                             "(not needed: the sheet has its own symbol panel)")
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve() if args.path else Path.cwd()
    opened = None
    if target.is_dir():
        root = target
    else:
        if target.suffix.lower() != ".ipynb":
            target = target.with_suffix(".ipynb")
        root, opened = target.parent, target.name
        if not root.is_dir():
            parser.error(f"folder not found: {root}")

    port = _free_port(args.port)
    config = Config(root, port, args.token or secrets.token_urlsafe(24),
                    palette=args.palette)
    url = f"http://127.0.0.1:{port}/?token={config.token}"
    if opened:
        url += "&open=" + quote(opened)

    launch = None
    if not args.no_browser:
        def launch(address):
            if not (args.app and _app_window(address)):
                webbrowser.open(address)

    # pyzmq's asyncio support needs a selector loop; Windows defaults to
    # the proactor.  Build the loop directly — the policy API that used
    # to select it is deprecated.
    try:
        if hasattr(asyncio, "Runner"):
            factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
            with asyncio.Runner(loop_factory=factory) as runner:
                runner.run(_serve(config, url, launch))
        else:                                   # Python 3.10
            loop = asyncio.SelectorEventLoop() if sys.platform == "win32" \
                else asyncio.new_event_loop()
            try:
                loop.run_until_complete(_serve(config, url, launch))
            finally:
                loop.close()
    except KeyboardInterrupt:
        print("\nworksheet server stopped")
    return 0
