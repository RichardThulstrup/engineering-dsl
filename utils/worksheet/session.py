"""One kernel per open worksheet, and the conversation with its engine.

The server hands a :class:`KernelSession` requests (plain dicts) and a
callback; engine replies come back through the callback as they are
produced.  The kernel is an ordinary IPython kernel started with the
interpreter the server itself runs on, so the worksheet calculates with
exactly the toolkit that is installed alongside it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import AsyncKernelManager

from .protocol import BOOTSTRAP, MIME, request_code

__all__ = ["KernelSession"]

# The checkout (or site-packages) this very package was imported from:
# the kernel must find the same ``utils`` whatever its working directory.
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])


class _Pending:
    """A request the kernel is working on."""
    __slots__ = ("seq", "future", "problem")

    def __init__(self, seq, future):
        self.seq, self.future, self.problem = seq, future, None

    def finish(self, problem):
        if problem and not self.problem:
            self.problem = problem
        if not self.future.done():
            self.future.set_result(None)


class KernelSession:
    def __init__(self, cwd: str, emit, *, palette: bool = False):
        self.cwd = cwd
        self.emit = emit                # callable(dict): message for the front end
        self.palette = palette          # let the toolkit start the Windows symbol palette?
        self.km = None
        self.kc = None
        self.state = "none"
        self.detail = None              # what went wrong, while the state is "dead"
        self._tasks = []
        self._waiting = {}              # msg_id -> _Pending
        self._lock = asyncio.Lock()     # one request in the kernel at a time
        self._queued_calc = None
        self._calc_running = False
        self._start_lock = asyncio.Lock()
        self._generation = 0
        self._closed = False            # set by close(): no kernel is started again

    # ---- lifecycle -----------------------------------------------------
    def _status(self, state, detail=None):
        self.state = state
        self.detail = detail
        message = {"op": "status", "kernel": state}
        if detail:
            message["detail"] = detail
        self.emit(message)

    async def start(self):
        async with self._start_lock:
            if self.km is not None or self._closed:
                return
            # ``shutdown()`` moves the generation on.  A start it overtakes
            # (the kernel was still coming up) is then abandoned: its
            # kernel is stopped rather than left running unowned.
            self._generation += 1
            generation = self._generation
            self._status("starting")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (_PACKAGE_ROOT, env.get("PYTHONPATH")) if p)
            env["PYTHONIOENCODING"] = "utf-8"
            env.setdefault("MPLBACKEND", "module://matplotlib_inline.backend_inline")
            if not self.palette:
                # The sheet has a symbol panel of its own, so the toolkit is
                # told not to start the Windows palette with the kernel.
                env["ENGINEER_HEADLESS"] = "1"
            km = AsyncKernelManager()
            # Built here rather than looked up, so no kernelspec needs to be
            # installed and the kernel is this very Python.  (Its routine
            # start-up warnings would only clutter the server's console.)
            km._kernel_spec = KernelSpec(
                argv=[sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}",
                      "--log-level=ERROR"],
                display_name="Engineering DSL worksheet", language="python")
            kc = None
            try:
                await km.start_kernel(env=env, cwd=self.cwd)
                kc = km.client()
                kc.start_channels()
                await kc.wait_for_ready(timeout=90)
                problem = None
            except Exception as exc:
                problem = f"The kernel did not start: {exc}"
            if problem or generation != self._generation:
                if generation == self._generation:
                    self._status("dead", problem)
                if kc is not None:
                    kc.stop_channels()
                try:
                    await km.shutdown_kernel(now=True)
                except Exception:
                    pass
                return
            self.km, self.kc = km, kc
            self._tasks = [asyncio.ensure_future(self._pump_iopub(kc, generation)),
                           asyncio.ensure_future(self._drain_shell(kc)),
                           asyncio.ensure_future(self._watch(km, generation))]
            problem = await self._execute(BOOTSTRAP, None)
            if generation != self._generation:
                return                      # shut down meanwhile: nothing to report
            if problem:
                self._status("dead", "The toolkit did not load in the kernel:\n" + problem)
                return
            self._status("idle")

    async def shutdown(self):
        self._generation += 1
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        for pending in self._waiting.values():
            pending.finish("kernel stopped")
        self._waiting.clear()
        kc, km, self.kc, self.km = self.kc, self.km, None, None
        self.state, self.detail = "none", None
        if kc is not None:
            kc.stop_channels()
        if km is not None:
            try:
                await km.shutdown_kernel(now=True)
            except Exception:
                pass

    async def close(self):
        """Shut down for good: the tab this session served is gone.

        Unlike ``shutdown()``, which the next request undoes by starting
        a kernel again, this also holds for a request still on its way.
        """
        self._closed = True
        self._queued_calc = None
        await self.shutdown()

    async def restart(self, cwd=None):
        if cwd:
            self.cwd = cwd              # at once: where this session works from now on
        self._queued_calc = None        # what was waiting was meant for the old kernel
        await self.shutdown()
        await self.start()

    async def interrupt(self):
        self._queued_calc = None
        if self.km is not None:
            try:
                await self.km.interrupt_kernel()
            except Exception as exc:
                self.emit({"op": "log", "text": f"interrupt failed: {exc}"})

    # ---- message pumps -------------------------------------------------
    async def _pump_iopub(self, kc, generation):
        while generation == self._generation:
            try:
                msg = await kc.get_iopub_msg(timeout=None)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(0.05)
                continue
            pending = self._waiting.get(msg["parent_header"].get("msg_id"))
            if pending is None:
                continue
            seq = pending.seq
            kind, content = msg["msg_type"], msg["content"]
            if kind == "status":
                if content.get("execution_state") == "idle":
                    pending.finish(None)
            elif kind in ("display_data", "execute_result"):
                data = content.get("data", {})
                if MIME in data:
                    self.emit(data[MIME])
                else:
                    self.emit({"op": "stray", "seq": seq, "type": "display",
                               "data": {k: v for k, v in data.items()
                                        if k in ("text/plain", "text/latex", "image/png")}})
            elif kind == "stream":
                self.emit({"op": "stray", "seq": seq, "type": "stream",
                           "name": content.get("name"), "text": content.get("text", "")})
            elif kind == "error":
                # An exception in the request itself (not in a region:
                # those are caught and reported by the engine).
                text = f"{content.get('ename')}: {content.get('evalue')}"
                self.emit({"op": "stray", "seq": seq, "type": "error", "text": text})
                pending.problem = text

    async def _drain_shell(self, kc):
        while True:
            try:
                await kc.get_shell_msg(timeout=None)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(0.05)

    async def _watch(self, km, generation):
        while generation == self._generation:
            await asyncio.sleep(2.0)
            try:
                alive = await km.is_alive()
            except Exception:
                alive = False
            if not alive and generation == self._generation:
                for pending in self._waiting.values():
                    pending.finish("kernel died")
                self._status("dead", "The kernel stopped. Restart it to continue.")
                return

    async def _execute(self, code, seq):
        """Run ``code`` in the kernel; returns an error text or ``None``."""
        kc = self.kc
        if kc is None:
            return "no kernel"
        pending = _Pending(seq, asyncio.get_running_loop().create_future())
        msg_id = kc.execute(code, store_history=False)
        self._waiting[msg_id] = pending
        try:
            await pending.future
        finally:
            self._waiting.pop(msg_id, None)
        return pending.problem

    # ---- requests ------------------------------------------------------
    async def request(self, message: dict):
        """Send one request to the engine and wait until it has replied.

        Calculation requests supersede each other: while one runs, only
        the newest of those waiting is kept — a sheet edited three times
        in quick succession is calculated for the last edit, not thrice.
        """
        if message.get("op") == "calc":
            self._queued_calc = message
            if self._calc_running:
                return
            self._calc_running = True
            try:
                while self._queued_calc is not None:
                    current, self._queued_calc = self._queued_calc, None
                    await self._send(current)
            finally:
                self._calc_running = False
        else:
            await self._send(message)

    async def _send(self, message: dict):
        if self.km is None:
            await self.start()
        if self.state == "dead" or self.kc is None:
            self.emit({"op": "done", "seq": message.get("seq"), "failed": True})
            return
        encoded = base64.b64encode(json.dumps(message).encode("utf-8")).decode("ascii")
        async with self._lock:
            if self.kc is None:
                return
            busy = message.get("op") == "calc"
            if busy and self.state == "idle":
                self._status("busy")
            problem = await self._execute(request_code(encoded), message.get("seq"))
            if busy and self.state == "busy":
                self._status("idle")
        if problem:
            self.emit({"op": "done", "seq": message.get("seq"), "failed": True,
                       "detail": problem})
