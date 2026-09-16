"""IPython kernel with an honest DSL language id for notebook editors.

Register it with ``python -m utils.vscode_kernel --install --user``.
The ordinary Engineer preamble and runtime hook remain unchanged.
"""
from pathlib import Path
import sys

# A kernelspec uses this file's absolute path, so source checkouts work even
# when the notebook lives elsewhere. This also works in an installed wheel.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipykernel.ipkernel import IPythonKernel


class EngineeringDSLKernel(IPythonKernel):
    implementation = "engineering-dsl"
    implementation_version = "0.1.0"
    language_info = {
        **IPythonKernel.language_info,
        "name": "engineering-dsl",
        "codemirror_mode": "python",
        "pygments_lexer": "python",
    }


def install(*, user=True, prefix=None):
    import json
    from tempfile import TemporaryDirectory
    from jupyter_client.kernelspec import KernelSpecManager

    spec = {
        "argv": [sys.executable, str(Path(__file__).resolve()), "-f", "{connection_file}"],
        "display_name": "Engineering DSL",
        "language": "engineering-dsl",
        "env": {"MPLBACKEND": "module://matplotlib_inline.backend_inline"},
    }
    with TemporaryDirectory(prefix="engineering-dsl-kernel-") as folder:
        Path(folder, "kernel.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return KernelSpecManager().install_kernel_spec(
            folder, kernel_name="engineering-dsl", user=user, prefix=prefix,
        )


def main():
    if "--install" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--install", action="store_true")
        destination = parser.add_mutually_exclusive_group()
        destination.add_argument("--user", action="store_true")
        destination.add_argument("--prefix")
        args = parser.parse_args()
        print(install(user=not args.prefix, prefix=args.prefix))
    else:
        from ipykernel.kernelapp import IPKernelApp
        IPKernelApp.launch_instance(kernel_class=EngineeringDSLKernel)


if __name__ == "__main__":
    main()
