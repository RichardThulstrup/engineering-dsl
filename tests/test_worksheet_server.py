"""The worksheet server runs code, so it answers only its own page.

One server (as a subprocess, the way it is really started) serves most
of the module, up to a calculation driven through it end to end; the
tests about how kernels are started get servers of their own.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import nbformat
from nbformat.v4 import new_code_cell, new_notebook
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def private(folder):
    """An environment that keeps Jupyter's and matplotlib's files in ``folder``."""
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                JUPYTER_DATA_DIR=str(folder / ".jupyter-data"),
                MPLCONFIGDIR=str(folder / ".matplotlib"), IPYTHONDIR=str(folder / ".ipython"))


@contextmanager
def launch(folder, *options):
    """A worksheet server on ``folder``, started the way a user starts it."""
    port = free_port()
    # What the server says goes into a file.  A pipe that nobody reads
    # holds some thirty lines of its log (every request that is turned
    # down is one), and the server stops for good at the line that no
    # longer fits: the test that asks next waits in vain.
    said = tempfile.TemporaryFile()
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "utils.worksheet", str(folder), "--no-browser", *options,
         "--port", str(port), "--token", TOKEN],
        cwd=str(ROOT), env=private(folder), stdout=said, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            process.kill()
            said.seek(0)
            pytest.fail("server did not start: " + said.read().decode("utf-8", "replace"))
        yield type("Server", (), {"port": port, "folder": folder, "base": f"http://127.0.0.1:{port}",
                                  "cookie": f"edsl_ws_{port}={TOKEN}"})
    finally:
        process.terminate()
        process.wait(timeout=15)
        said.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sheets")
    nbformat.write(new_notebook(cells=[new_code_cell("from utils.Engineer import *"),
                                       new_code_cell("x := 2 V")]), str(folder / "a.ipynb"))
    with launch(folder) as running:
        yield running


def fetch(server, path, *, cookie=True, method="GET", body=None, headers=None, follow=False):
    """``(status, headers, text)`` — without following redirects unless asked."""
    request = urllib.request.Request(server.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
    if cookie:
        request.add_header("Cookie", server.cookie)
    for name, value in (headers or {}).items():
        request.add_header(name, value)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(*([] if follow else [NoRedirect]))
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode("utf-8", "replace")


def test_the_page_needs_the_token_once_and_the_cookie_after(server):
    assert fetch(server, "/", cookie=False)[0] == 403
    assert fetch(server, "/?token=wrong", cookie=False)[0] == 403
    status, headers, _ = fetch(server, f"/?token={TOKEN}&open=a.ipynb", cookie=False)
    assert status == 302 and headers["Location"] == "/?open=a.ipynb"       # token leaves the URL
    assert TOKEN in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    status, _, page = fetch(server, "/")
    assert status == 200 and "worksheet.css" in page


def test_the_api_is_closed_without_the_cookie(server):
    for path in ("/api/list", "/api/doc?path=a.ipynb", "/api/info"):
        assert fetch(server, path, cookie=False)[0] == 403
    assert fetch(server, "/api/list")[0] == 200


def test_requests_for_another_host_or_from_another_origin_are_refused(server):
    # A page elsewhere that points a host name at 127.0.0.1 (DNS rebinding) …
    assert fetch(server, "/api/list", headers={"Host": "evil.example"})[0] == 403
    # … or that posts across origins with the user's cookie.
    assert fetch(server, "/api/doc?path=b.ipynb", method="PUT", body={"doc": {"regions": []}},
                 headers={"Origin": "http://evil.example"})[0] == 403
    assert not (server.folder / "b.ipynb").exists()


def test_documents_are_read_and_written_inside_the_folder_only(server):
    status, _, text = fetch(server, "/api/doc?path=a.ipynb")
    doc = json.loads(text)
    assert status == 200 and doc["regions"][0]["source"] == "x := 2 V" and doc["path"] == "a.ipynb"
    assert isinstance(doc["mtime"], str)                    # survives JavaScript numbers
    assert fetch(server, "/api/doc?path=../outside.ipynb")[0] == 403
    assert fetch(server, "/api/doc?path=a.txt")[0] == 400
    assert fetch(server, "/api/doc?path=missing.ipynb")[0] == 404


def test_saving_creates_refuses_to_clobber_and_detects_conflicts(server):
    doc = {"regions": [{"id": "r1", "kind": "math", "source": "y := 3 A", "x": 0, "y": 0}]}
    status, _, text = fetch(server, "/api/doc?path=new.ipynb", method="PUT", body={"doc": doc, "create": True})
    saved = json.loads(text)
    assert status == 200 and saved["path"] == "new.ipynb"
    again = fetch(server, "/api/doc?path=new.ipynb", method="PUT", body={"doc": doc, "create": True})
    assert again[0] == 409 and json.loads(again[2]) == {"exists": True}
    stale = fetch(server, "/api/doc?path=new.ipynb", method="PUT", body={"doc": doc, "mtime": "1"})
    assert stale[0] == 409 and json.loads(stale[2]) == {"conflict": True}
    assert fetch(server, "/api/doc?path=new.ipynb", method="PUT",
                 body={"doc": doc, "mtime": saved["mtime"]})[0] == 200


def test_static_files_are_served_and_revalidated(server):
    status, headers, text = fetch(server, "/static/js/main.js", cookie=False)
    assert status == 200 and "no-cache" in headers["Cache-Control"] and "Conn" in text
    assert fetch(server, "/static/../server.py", cookie=False)[0] in (403, 404)
    # Scripts are served as scripts whatever this machine's registry says:
    # a browser will not run a module that arrives as plain text.
    for script in ("/static/js/main.js", "/static/vendor/mathlive/mathlive.min.mjs"):
        assert fetch(server, script, cookie=False)[1]["Content-Type"].startswith("text/javascript")
    # The page names its icon, which arrives as an icon.
    assert '<link rel="icon" href="/static/icon.ico">' in fetch(server, "/")[2]
    status, headers, _ = fetch(server, "/static/icon.ico", cookie=False, method="HEAD")
    assert status == 200 and headers["Content-Type"] == "image/x-icon"
    # A browser that holds the same bytes under another type (from a server
    # that left the type to the registry) is not told "not modified" — that
    # answer has no type, and would leave the script unusable for good.
    module = ROOT / "utils" / "worksheet" / "static" / "vendor" / "mathlive" / "mathlive.min.mjs"
    by_bytes_alone = '"%s"' % hashlib.sha512(module.read_bytes()).hexdigest()
    status, headers, _ = fetch(server, "/static/vendor/mathlive/mathlive.min.mjs", cookie=False,
                               headers={"If-None-Match": by_bytes_alone})
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    assert fetch(server, "/static/vendor/mathlive/mathlive.min.mjs", cookie=False,
                 headers={"If-None-Match": headers["ETag"]})[0] == 304         # its own validator still saves the transfer


def test_a_front_end_file_that_changes_under_a_running_server_is_served_anew(tmp_path, monkeypatch):
    # An updated toolkit starts new kernels at once; the page must not be
    # told that its old scripts are still current.
    from tornado.httpclient import AsyncHTTPClient
    from tornado.httpserver import HTTPServer
    from utils.worksheet import server as worksheet

    script = tmp_path / "app.js"
    script.write_text("old", encoding="utf-8")
    monkeypatch.setattr(worksheet, "STATIC", tmp_path)
    port = free_port()

    async def reload():
        listening = HTTPServer(worksheet.make_app(worksheet.Config(tmp_path, port, TOKEN)))
        listening.listen(port, address="127.0.0.1")
        try:
            fetch_ = AsyncHTTPClient().fetch
            first = await fetch_(f"http://127.0.0.1:{port}/static/app.js")
            script.write_text("new, and longer", encoding="utf-8")
            again = await fetch_(f"http://127.0.0.1:{port}/static/app.js", raise_error=False,
                                 headers={"If-None-Match": first.headers["ETag"]})     # as a reload asks
            return first.body, again.code, again.body
        finally:
            listening.stop()

    assert asyncio.run(reload()) == (b"old", 200, b"new, and longer")


def socket_session(server, messages, *, cookie=True, origin=None, until="done", timeout=90,
                   path="a.ipynb"):
    """Open the WebSocket for the sheet at ``path``, send ``messages``, and
    collect replies up to ``until`` — or just the first, with no messages."""
    from tornado.httpclient import HTTPRequest
    from tornado.websocket import websocket_connect

    async def run():
        headers = {"Origin": origin or server.base}
        if cookie:
            headers["Cookie"] = server.cookie
        request = HTTPRequest(f"ws://127.0.0.1:{server.port}/ws?session=s1&path={path}",
                              headers=headers)
        connection = await websocket_connect(request)
        replies = []
        for message in messages:
            await connection.write_message(json.dumps(message))
        while True:
            raw = await asyncio.wait_for(connection.read_message(), timeout)
            if raw is None:
                replies.append({"op": "closed", "code": connection.close_code})
                break
            reply = json.loads(raw)
            replies.append(reply)
            if not messages or (reply.get("op") == until and reply.get("seq") == messages[-1].get("seq")):
                break
        connection.close()
        return replies

    return asyncio.run(run())


def test_the_socket_is_closed_to_strangers(server):
    from tornado.httpclient import HTTPClientError

    assert socket_session(server, [], cookie=False)[-1] == {"op": "closed", "code": 4403}
    with pytest.raises(HTTPClientError):                    # refused at the handshake
        socket_session(server, [], origin="http://evil.example")


def test_a_calculation_through_the_whole_stack(server):
    replies = socket_session(server, [
        {"op": "hello", "seq": 1},
        {"op": "calc", "seq": 2, "regions": [{"id": "r1", "source": "x := 2 V"},
                                             {"id": "r2", "source": "p := x · 3 A"}]},
    ])
    kinds = [r["op"] for r in replies]
    assert kinds[0] == "status"
    hello = next(r for r in replies if r["op"] == "hello")
    assert "kΩ" in hello["units"] and "sqrt" in hello["functions"]
    regions = {r["id"]: r for r in replies if r["op"] == "region"}
    assert regions["r2"]["items"][0]["result"][0]["text/plain"] == "6 W"
    assert r"\coloneqq" in regions["r2"]["items"][0]["latex"]
    done = replies[-1]
    assert done["op"] == "done" and done["seq"] == 2 and done["names"] == ["p", "x"]


def test_a_sheets_values_are_asked_of_its_kernel_and_handed_to_another_sheets_page(server):
    # (A page asks whether the server it talks to does this: one started before it could does not say so.)
    assert json.loads(fetch(server, "/api/info")[2])["values"] is True
    # What the variables of a sheet are is the kernel's to say ...
    replies = socket_session(server, [
        {"op": "calc", "seq": 1, "regions": [{"id": "r1", "source": "x := 2 V\nf := t → t²"}]},
        {"op": "values", "seq": 2}])
    said = next(r for r in replies if r["op"] == "values")
    assert [entry["name"] for entry in said["values"]] == ["x", "f"] and said["values"][1] == {"name": "f", "tex": "f", "why": "a function"}
    assert said["values"][0]["shown"]["text/plain"] == "2 V" and replies[-1] == {"op": "done", "seq": 2}
    # ... and what they were when it was saved is the file's, which the
    # server reads for the page of a sheet that wants them.
    assert fetch(server, "/api/values?path=a.ipynb", cookie=False)[0] == 403
    assert json.loads(fetch(server, "/api/values?path=a.ipynb")[2]) == {"name": "a", "revision": None, "saved": "", "values": None}
    record = {"revision": 1, "saved": "2026-10-11T10:00:00.000Z", "toolkit": said["toolkit"], "values": said["values"]}
    doc = {"regions": [{"id": "r1", "kind": "math", "source": "x := 2 V\nf := t → t²", "x": 0, "y": 0}],
           "settings": {"about": {"revision": 1, "saved": record["saved"]}}, "values": record}
    (server.folder / "deep").mkdir()
    status, _, text = fetch(server, "/api/doc?path=deep/source.ipynb", method="PUT", body={"doc": doc, "create": True})
    assert status == 200 and json.loads(text)["values"] == 1
    try:
        status, _, text = fetch(server, "/api/values?path=deep/source.ipynb")
        assert status == 200 and json.loads(text) == {"name": "source", "revision": 1, "saved": record["saved"], "values": record}
        brief = json.loads(fetch(server, "/api/values?path=deep/source.ipynb&brief=1")[2])
        assert brief["values"] == {"revision": 1, "saved": record["saved"], "count": 1} and brief["revision"] == 1
        # Only notebooks in the folder, and only ones that can be read.
        assert fetch(server, "/api/values?path=../outside.ipynb")[0] == 403
        assert fetch(server, "/api/values?path=deep/source.txt")[0] == 400
        assert fetch(server, "/api/values?path=missing.ipynb")[0] == 404
        (server.folder / "deep" / "broken.ipynb").write_text("{ not a notebook", encoding="utf-8")
        status, _, text = fetch(server, "/api/values?path=deep/broken.ipynb")
        assert status == 422 and "not a readable notebook" in json.loads(text)["error"]
    finally:
        for name in ("source.ipynb", "broken.ipynb"):
            (server.folder / "deep" / name).unlink(missing_ok=True)
        (server.folder / "deep").rmdir()


# ---------------------------------------------------------------------------
# kernels: one is started ahead, so a sheet is calculated sooner
# ---------------------------------------------------------------------------

WHERE = "%%python\nimport os\nos.path.basename(os.getcwd())"


def shown(replies):
    """The value the (single) region of a calculation ended with."""
    region = next(r for r in replies if r["op"] == "region")
    return region["items"][-1]["result"][0]["text/plain"]


def test_a_kernel_is_already_starting_when_the_first_tab_asks(tmp_path):
    with launch(tmp_path) as fresh:
        first = socket_session(fresh, [])[0]
    # "none" would mean that nothing had been started for this tab yet.
    assert first["op"] == "status" and first["kernel"] in ("starting", "idle")


def test_a_sheet_in_another_folder_gets_a_kernel_that_works_there(tmp_path):
    (tmp_path / "sub").mkdir()
    with launch(tmp_path) as fresh:
        replies = socket_session(fresh, [
            {"op": "hello", "seq": 1},
            {"op": "calc", "seq": 2, "regions": [{"id": "r1", "source": WHERE}]},
        ], path="sub/b.ipynb")
    assert replies[0] == {"op": "status", "kernel": "none"}     # not the one started ahead
    assert shown(replies) == "'sub'"


def test_a_tab_that_moves_to_a_sheet_in_another_folder_gets_a_kernel_there(tmp_path):
    (tmp_path / "sub").mkdir()
    where = [{"op": "hello", "seq": 1},
             {"op": "calc", "seq": 2, "regions": [{"id": "r1", "source": WHERE}]}]
    with launch(tmp_path) as fresh:
        first = socket_session(fresh, where)                        # the tab's first sheet
        moved = socket_session(fresh, where, path="sub/b.ipynb")    # same tab, a sheet elsewhere
        stayed = socket_session(fresh, where, path="sub/c.ipynb")   # and another one beside it
    assert shown(first) == repr(tmp_path.name)
    assert moved[0] == {"op": "status", "kernel": "none"} and shown(moved) == "'sub'"
    assert stayed[0] == {"op": "status", "kernel": "idle"} and shown(stayed) == "'sub'"


@pytest.fixture
def kernels(tmp_path, monkeypatch):
    """Run a scenario with a ``KernelSession`` in ``tmp_path/here``; the
    kernel managers it creates are recorded, oldest first."""
    from utils.worksheet import session

    for name, value in private(tmp_path).items():
        monkeypatch.setenv(name, value)
    (tmp_path / "here").mkdir()
    (tmp_path / "there").mkdir()
    managers = []

    class Recorded(session.AsyncKernelManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            managers.append(self)

    monkeypatch.setattr(session, "AsyncKernelManager", Recorded)

    def run(scenario, **options):
        async def main():
            messages = []
            kernel = session.KernelSession(str(tmp_path / "here"), messages.append, **options)
            starting = asyncio.ensure_future(kernel.start())
            while not (managers and managers[0].has_kernel):
                await asyncio.sleep(0.02)                    # its process exists; it is not ready
            try:
                return await scenario(kernel, starting, managers), messages
            finally:
                await kernel.shutdown()

        if sys.platform == "win32" and hasattr(asyncio, "Runner"):     # as the server does: pyzmq
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                return runner.run(main())
        return asyncio.run(main())

    run.there = str(tmp_path / "there")
    return run


def test_a_kernel_shut_down_while_it_starts_is_not_left_running(kernels):
    async def scenario(kernel, starting, managers):
        await kernel.shutdown()
        await starting
        return kernel.km, kernel.state, await managers[0].is_alive()

    (owned, state, alive), messages = kernels(scenario)
    assert owned is None and state == "none" and not alive
    assert [m["kernel"] for m in messages] == ["starting"]      # and no failure reported


def test_a_kernel_shut_down_while_the_toolkit_loads_reports_no_failure(kernels):
    async def scenario(kernel, starting, managers):
        while kernel.km is None:
            await asyncio.sleep(0.02)                        # ready, and loading the toolkit
        await kernel.shutdown()
        await starting
        return kernel.state, await managers[0].is_alive()

    (state, alive), messages = kernels(scenario)
    assert state == "none" and not alive
    assert [m["kernel"] for m in messages] == ["starting"]


def test_a_restart_in_another_folder_while_the_kernel_starts_ends_up_there(kernels):
    async def scenario(kernel, starting, managers):
        await kernel.restart(kernels.there)
        await starting
        await kernel.request({"op": "calc", "seq": 1, "regions": [{"id": "r1", "source": WHERE}]})
        return [await manager.is_alive() for manager in managers]

    alive, messages = kernels(scenario)
    assert alive == [False, True]
    assert shown(messages) == "'there'"
    assert [m["kernel"] for m in messages if m["op"] == "status"] == [
        "starting", "starting", "idle", "busy", "idle"]


def test_a_closed_session_does_not_start_a_kernel_again(kernels):
    slow = {"op": "calc", "seq": 1,
            "regions": [{"id": "r1", "source": "%%python\nimport time\ntime.sleep(1)"}]}

    async def scenario(kernel, starting, managers):
        await starting
        running = asyncio.ensure_future(kernel.request(slow))
        await asyncio.sleep(0.3)
        await kernel.request(dict(slow, seq=2))             # waits its turn behind the first
        await kernel.close()                                # the tab is gone
        await running
        await kernel.request({"op": "hello", "seq": 3})     # and a straggler arrives
        return [await manager.is_alive() for manager in managers]

    alive, messages = kernels(scenario)
    assert alive == [False]                                 # the one kernel, stopped; no other
    assert messages[-1] == {"op": "done", "seq": 3, "failed": True}


# ---------------------------------------------------------------------------
# the Windows symbol palette: a sheet has its own panel, so it is not started
# ---------------------------------------------------------------------------

SWITCH = '%%python\nimport os\nos.environ.get("ENGINEER_HEADLESS", "not set")'


@pytest.fixture
def unstartable_palette(tmp_path, monkeypatch):
    """Stand in for the palette with a file that is there but is no program.

    An attempt to start it fails, and the toolkit says so: the attempt
    shows without anything ever being started.
    """
    stand_in = tmp_path / "palette.txt"
    stand_in.write_text("not a program", encoding="utf-8")
    monkeypatch.setenv("SYMBOL_PALETTE_EXE", str(stand_in))
    monkeypatch.delenv("ENGINEER_HEADLESS", raising=False)


def said_about_the_palette(messages):
    return "".join(m["text"] for m in messages if m["op"] == "stray" and "palette" in m.get("text", ""))


async def started(kernel, starting, managers):
    await starting
    return kernel.state


def test_a_sheets_kernel_does_not_try_to_start_the_symbol_palette(kernels, unstartable_palette):
    state, messages = kernels(started)
    assert state == "idle" and said_about_the_palette(messages) == ""


def test_a_kernel_that_may_start_the_symbol_palette_does_so_as_a_notebook_would(kernels, unstartable_palette):
    state, messages = kernels(started, palette=True)
    assert state == "idle" and "could not launch palette" in said_about_the_palette(messages)


def test_the_server_leaves_the_symbol_palette_alone_unless_started_with_its_option(
        server, tmp_path, unstartable_palette):
    from utils.worksheet import server as worksheet

    asked = [{"op": "hello", "seq": 1},
             {"op": "calc", "seq": 2, "regions": [{"id": "r1", "source": SWITCH}]}]
    assert shown(socket_session(server, asked)) == "'1'"
    assert not worksheet.Config(tmp_path, server.port, TOKEN).palette      # nor one set up in code
    with launch(tmp_path, "--palette") as with_palette:
        assert shown(socket_session(with_palette, asked)) == "'not set'"


# ---------------------------------------------------------------------------
# --app: the sheet in a window of its own
# ---------------------------------------------------------------------------

def test_the_app_window_prefers_an_installed_browser_to_one_on_the_path(tmp_path, monkeypatch):
    # "chrome" on a developer's PATH may be a years-old build kept for automated tests.
    from utils.worksheet import server as worksheet

    installed = tmp_path / "Program Files" / "Google" / "Chrome" / "Application" / "chrome.exe"
    on_path = tmp_path / "tools" / "chrome.exe"
    for browser in (installed, on_path):
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"")
    for name in ("ProgramFiles(x86)", "LocalAppData"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setattr(worksheet.shutil, "which", lambda name: str(on_path) if name == "chrome" else None)
    started = []
    monkeypatch.setattr(worksheet.subprocess, "Popen", started.append)      # recorded, not carried out

    assert worksheet._app_window("http://127.0.0.1:1/?token=t")
    assert started == [[str(installed), "--app=http://127.0.0.1:1/?token=t"]]
    installed.unlink()                              # none installed: the one on the PATH will do
    assert worksheet._app_window("http://127.0.0.1:1/") and started[-1][0] == str(on_path)
    on_path.unlink()                                # no such browser at all: the caller opens a tab
    assert not worksheet._app_window("http://127.0.0.1:1/") and len(started) == 2


# ---------------------------------------------------------------------------
# starting without the packages the worksheet needs
# ---------------------------------------------------------------------------

def test_a_missing_package_is_named_instead_of_a_traceback(monkeypatch):
    import importlib.util
    from utils import worksheet
    from utils.worksheet import server

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *more: None if name == "tornado" else real(name, *more))
    monkeypatch.setattr(server, "main", lambda argv=None: pytest.fail("the server was started all the same"))
    with pytest.raises(SystemExit) as stopped:
        worksheet.main(["--no-browser"])
    said, how = str(stopped.value).split("Install with:")
    assert "needs tornado:" in said                         # what is missing, and only that
    assert all(name in how for name in worksheet.NEEDS)     # and a command that brings all of it


# ---------------------------------------------------------------------------
# how the page is set up: kept on this computer, not in the sheet
# ---------------------------------------------------------------------------

def test_what_was_chosen_for_the_page_is_kept_for_the_next_server(server):
    assert fetch(server, "/api/prefs", cookie=False)[0] == 403
    assert fetch(server, "/api/prefs", cookie=False, method="PUT", body={"grid": True})[0] == 403
    status, _, text = fetch(server, "/api/prefs")
    # No grid until asked for; text is written as it looks, not as its markdown;
    # a new sheet has no header or footer.
    assert status == 200 and json.loads(text) == {"grid": False, "symbols": False, "markdown": False, "template": ""}
    # A choice is taken over; what is not a preference, or not of its kind, is not.
    status, _, text = fetch(server, "/api/prefs", method="PUT",
                            body={"grid": True, "theme": "dark", "symbols": "yes", "template": 3})
    assert status == 200 and json.loads(text) == {"grid": True, "symbols": False, "markdown": False, "template": ""}
    assert json.loads(fetch(server, "/api/prefs", method="PUT", body={"markdown": True})[2])["markdown"] is True
    assert json.loads(fetch(server, "/api/prefs", method="PUT", body={"template": "Title block"})[2])["template"] == "Title block"
    assert fetch(server, "/api/prefs", method="PUT", body=["grid"])[0] == 400
    try:
        # Another server on the same computer -- on another port, as a second
        # window of the app is -- finds it.
        with launch(server.folder) as other:
            assert other.port != server.port
            assert json.loads(fetch(other, "/api/prefs")[2]) == {"grid": True, "symbols": False, "markdown": True,
                                                                 "template": "Title block"}
    finally:
        fetch(server, "/api/prefs", method="PUT", body={"grid": False, "markdown": False, "template": ""})
    assert json.loads(fetch(server, "/api/prefs")[2])["grid"] is False


def test_headers_and_footers_of_the_users_own_are_kept_by_name(server):
    # A template is the header and footer of a page as a sheet holds them.
    # What one may hold is the page's to say: the server keeps what it is
    # given, as long as that is a JSON object under a name that is one.
    frame = {"header": ["{logo}", "**{title}**", "{saved}"], "footer": ["", "{page} / {pages}", ""],
             "rule": {"header": True, "footer": False}, "logo": {"data": {"image/png": "iVBORw0KGgo="}, "height": 12}}
    listed = lambda reply: [entry["name"] for entry in json.loads(reply[2])["templates"]]                # noqa: E731
    put = lambda body: fetch(server, "/api/templates", method="PUT", body=body)                         # noqa: E731
    assert fetch(server, "/api/templates", cookie=False)[0] == 403
    assert fetch(server, "/api/templates", cookie=False, method="PUT", body={"name": "x", "frame": frame})[0] == 403
    assert fetch(server, "/api/templates?name=x", cookie=False, method="DELETE")[0] == 403
    assert json.loads(fetch(server, "/api/templates")[2]) == {"templates": []}
    try:
        status, _, text = put({"name": "  Calculations  ", "frame": frame})
        assert status == 200 and json.loads(text) == {"templates": [{"name": "Calculations", "frame": frame}]}
        assert listed(put({"name": "acme", "frame": {"header": ["ACME", "", ""]}})) == ["acme", "Calculations"]
        assert listed(put({"name": "Æble ½", "frame": frame})) == ["acme", "Calculations", "Æble ½"]    # (by name, whatever the letters)
        # Another of the same name takes its place.
        status, _, text = put({"name": "acme", "frame": {"footer": ["", "", "{page}"]}})
        assert json.loads(text)["templates"][0] == {"name": "acme", "frame": {"footer": ["", "", "{page}"]}}
        assert listed((status, _, text)) == ["acme", "Calculations", "Æble ½"]
        # What is no template is turned down, and says why; the others are as they were.
        for body in ({"name": "", "frame": frame}, {"name": "   ", "frame": frame}, {"name": "x" * 61, "frame": frame},
                     {"name": "two\nlines", "frame": frame}, {"name": 7, "frame": frame}, {"frame": frame},
                     {"name": "no frame"}, {"name": "a list", "frame": [frame]}, {"name": "text", "frame": "{}"},
                     ["acme", frame], "acme",
                     {"name": "too large", "frame": {"logo": {"data": {"image/png": "A" * 4_000_000}}}}):
            status, _, text = put(body)
            assert status == 400 and json.loads(text)["error"], body if len(str(body)) < 200 else "the large one"
        assert "2 MB" in json.loads(put({"name": "too large", "frame": {"logo": {"data": {"image/png": "A" * 4_000_000}}}})[2])["error"]
        assert listed(fetch(server, "/api/templates")) == ["acme", "Calculations", "Æble ½"]
        # Another server on the same computer finds them: they are in a file beside the preferences.
        with launch(server.folder) as other:
            assert listed(fetch(other, "/api/templates")) == ["acme", "Calculations", "Æble ½"]
        stored = json.loads((server.folder / ".jupyter-data" / "edsl_worksheet_templates.json").read_text(encoding="utf-8"))
        assert [entry["name"] for entry in stored["templates"]] == ["acme", "Calculations", "Æble ½"]
        # One is let go of by its name; a name that is not there changes nothing.
        assert listed(fetch(server, "/api/templates?name=Calculations", method="DELETE")) == ["acme", "Æble ½"]
        assert listed(fetch(server, "/api/templates?name=Calculations", method="DELETE")) == ["acme", "Æble ½"]
        assert listed(fetch(server, "/api/templates", method="DELETE")) == ["acme", "Æble ½"]
        # A file that someone wrote by hand, and badly, costs no more than what is wrong in it.
        file = server.folder / ".jupyter-data" / "edsl_worksheet_templates.json"
        file.write_text(json.dumps({"templates": [{"name": "good", "frame": frame}, {"name": "", "frame": frame}, "x",
                                                  {"name": "no frame"}, {"name": "list", "frame": []}, None]}), encoding="utf-8")
        assert listed(fetch(server, "/api/templates")) == ["good"]
        for broken in ("{", "[]", '{"templates": 5}', ""):
            file.write_text(broken, encoding="utf-8")
            assert json.loads(fetch(server, "/api/templates")[2]) == {"templates": []}, broken
    finally:
        (server.folder / ".jupyter-data" / "edsl_worksheet_templates.json").unlink(missing_ok=True)


def test_no_more_templates_are_kept_than_there_is_use_for(tmp_path, monkeypatch):
    from utils.worksheet import templates

    monkeypatch.setattr(templates, "_file", lambda: tmp_path / "kept" / "templates.json")
    monkeypatch.setattr(templates, "MOST", 3)
    for name in "abc":
        templates.save(name, {"header": [name, "", ""]})
    with pytest.raises(ValueError, match="delete one first"):
        templates.save("d", {})
    assert [entry["name"] for entry in templates.save("b", {"again": True})] == ["a", "b", "c"]         # (one of them, again, is none more)
    assert templates.load()[1]["frame"] == {"again": True}
    assert [entry["name"] for entry in templates.delete("a")] == ["b", "c"]
    assert [entry["name"] for entry in templates.save("d", {})] == ["b", "c", "d"]
