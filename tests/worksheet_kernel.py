"""A kernel with the worksheet engine installed, for the worksheet tests.

Talks to the engine the way the server does — requests in, the engine's
``display_data`` replies out — but synchronously and without a server.
"""
import base64
import json
import os
from pathlib import Path
import sys

from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpec

from utils.worksheet.protocol import BOOTSTRAP, MIME, request_code

ROOT = Path(__file__).resolve().parents[1]


class Kernel:
    def __init__(self, tmp):
        env = dict(os.environ, ENGINEER_HEADLESS="1", PYTHONIOENCODING="utf-8",    # as the server starts it
                   PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT),
                   MPLBACKEND="module://matplotlib_inline.backend_inline",
                   MPLCONFIGDIR=str(tmp / "matplotlib"), IPYTHONDIR=str(tmp / "ipython"))
        self.manager = KernelManager()
        self.manager._kernel_spec = KernelSpec(         # this Python; no installed spec needed
            argv=[sys.executable, "-B", "-m", "ipykernel_launcher", "-f", "{connection_file}",
                  "--log-level=ERROR"], display_name="worksheet test", language="python")
        self.manager.start_kernel(env=env, cwd=str(tmp))
        self.client = self.manager.client()
        self.client.start_channels()
        self.client.wait_for_ready(timeout=120)
        self.seq = 0
        problems = [m for m in self.execute(BOOTSTRAP) if m.get("op") == "kernel-error"]
        assert not problems, problems

    def execute(self, code, timeout=120):
        """Run code; returns the engine's messages (and any kernel error)."""
        msg_id = self.client.execute(code, store_history=False)
        messages = []
        while True:
            msg = self.client.get_iopub_msg(timeout=timeout)
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            kind, content = msg["msg_type"], msg["content"]
            if kind == "status" and content["execution_state"] == "idle":
                break
            if kind == "display_data" and MIME in content["data"]:
                messages.append(content["data"][MIME])
            elif kind == "error":
                messages.append({"op": "kernel-error",
                                 "text": f"{content['ename']}: {content['evalue']}"})
        self.client.get_shell_msg(timeout=timeout)
        return messages

    def request(self, message, timeout=120):
        self.seq += 1
        payload = json.dumps(dict(message, seq=self.seq)).encode("utf-8")
        replies = self.execute(request_code(base64.b64encode(payload).decode("ascii")), timeout)
        faults = [m for m in replies if m.get("op") in ("fault", "kernel-error")]
        assert not faults, faults
        return replies

    def calc(self, sources, **options):
        """Calculate a sheet of region sources; returns a :class:`Pass`."""
        regions = [{"id": f"r{i}", "source": s} for i, s in enumerate(sources)]
        return Pass(self.request(dict({"op": "calc", "regions": regions}, **options)))

    def close(self):
        self.client.stop_channels()
        self.manager.shutdown_kernel(now=True)


class Pass:
    """The replies to one calculation, arranged for assertions."""

    def __init__(self, replies):
        self.replies = replies
        self.regions = [m for m in replies if m["op"] == "region"]
        self.done = next(m for m in replies if m["op"] == "done")
        self.executed = self.done.get("executed")
        self.reused = self.done.get("reused")

    def items(self, region=0):
        return [i for i in self.regions[region]["items"] if i["t"] == "stmt"]

    def item(self, region=0, index=0):
        return self.items(region)[index]

    def value(self, region=0, index=-1):
        """Plain-text value shown by a statement (``None`` if none)."""
        result = self.items(region)[index].get("result")
        return result[0].get("text/plain") if result else None

    def error(self, region=0, index=-1):
        error = self.items(region)[index].get("error")
        return error["ename"] if error else None

    def values(self):
        """Every statement's source → value or ``!Error``, sheet-wide."""
        shown = {}
        for region in self.regions:
            for item in region.get("items", ()):
                if item["t"] != "stmt":
                    continue
                error, result = item.get("error"), item.get("result")
                shown[item["src"]] = ("!" + error["ename"] if error
                                      else result[0].get("text/plain") if result else None)
        return shown
