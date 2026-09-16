"""Notebook input transformation and explicitly opted-in DSL imports."""
import sys
from importlib.machinery import PathFinder, SourceFileLoader
from importlib.util import decode_source

_SYNTAX_MODES = {"python", "legacy"}
_syntax_mode = "python"


def get_syntax_mode():
    return _syntax_mode


def validate_syntax_mode(mode):
    if mode not in _SYNTAX_MODES:
        raise ValueError("syntax mode must be 'python' or 'legacy'")
    return mode


def set_syntax_mode(mode):
    """Select assignment/list semantics for subsequent DSL cells."""
    global _syntax_mode
    _syntax_mode = validate_syntax_mode(mode)


def _input_transformer(lines):
    from .circuit_dsl import transform_source

    source = "".join(lines)
    first, _, rest = source.partition("\n")
    header = first.strip()
    if header == "%%python":
        return rest.splitlines(keepends=True)
    if header == "%%dsl" or header.startswith("%%dsl "):
        mode = header[5:].strip() or get_syntax_mode()
        return transform_source(rest, syntax_mode=mode).splitlines(keepends=True)
    if header.startswith("%%"):
        return lines  # IPython owns other cell magics and their bodies.
    if any(line.lstrip().startswith(("%", "!", "?")) for line in lines):
        return lines  # IPython handles line magics and shell escapes.
    return transform_source(source).splitlines(keepends=True)


class _DSLLoader(SourceFileLoader):
    def get_code(self, fullname):
        from .circuit_dsl import transform_source

        # Never read/write normal Python bytecode for transformed source.
        filename = self.get_filename(fullname)
        source = decode_source(self.get_data(filename))
        source = transform_source(source, filename=filename, transform_module=True)
        return compile(source, filename, "exec")


class _DSLModuleFinder:
    def __init__(self, modules):
        self.modules = set(modules)

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.modules:
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is None or not isinstance(spec.loader, SourceFileLoader):
            return None
        spec.loader = _DSLLoader(fullname, spec.origin)
        return spec

    def remove(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)


_module_finder = None


def install_hook(*, modules=(), syntax_mode=None):
    """Install once per shell; opt in exact module names separately.

    No global finder is installed unless modules are explicitly given.
    Returns that finder (with remove()) or the notebook callback.
    """
    global _module_finder
    if isinstance(modules, str):
        raise TypeError("modules must be a sequence of full module names")
    modules = tuple(modules)
    if any(not isinstance(name, str) or not name for name in modules):
        raise ValueError("modules must contain non-empty full module names")
    if syntax_mode is not None:
        set_syntax_mode(syntax_mode)
    from IPython import get_ipython
    shell = get_ipython()
    if shell is not None and _input_transformer not in shell.input_transformers_cleanup:
        shell.input_transformers_cleanup.append(_input_transformer)
    if modules:
        if _module_finder is None:
            _module_finder = _DSLModuleFinder(modules)
        else:
            _module_finder.modules.update(modules)
        if _module_finder not in sys.meta_path:
            sys.meta_path.insert(0, _module_finder)
        return _module_finder
    return _input_transformer
