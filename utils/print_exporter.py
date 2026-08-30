"""Custom nbconvert exporters: the toolkit's own hardcopy rendering.

Registered as nbconvert entry points (see ``pyproject.toml``), which
makes them available everywhere nbconvert looks up exporters by name —
with **no server extension and no configuration**:

* ``jupyter nbconvert --to edsl_print notebook.ipynb`` on the CLI,
* ``http://<server>/nbconvert/edsl_print/<notebook>`` on any running
  Jupyter server (this is what ``print_view()`` opens — a Voila-style
  rendered view, but from the saved file, so no re-execution cost),
* JupyterLab's *File → Save and Export Notebook As* menu,
* ``hardcopy()`` / ``hardcopy.py`` via the ``edsl_pdf`` variant.

Compared with the stock "lab" rendering the ``edsl-print`` template
(in ``templates/edsl-print/``) adds:

* code coloured by the DSL's own Pygments lexer and style
  (``Engineer_Style``) instead of generic Python highlighting,
* the in-notebook print controls (``print_view()`` output) hidden,
* cells tagged ``no-print`` removed entirely (add tags via the cell's
  property inspector in JupyterLab),
* print pagination niceties (no page break right after a heading, no
  break inside a code block or figure).

After ``pip install -e .`` the entry points exist; a running Jupyter
server must be restarted once to see them.
"""

import os

from traitlets import Enum, default
from traitlets.config import Config

from nbconvert.exporters.html import HTMLExporter
from nbconvert.exporters.webpdf import WebPDFExporter

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _dsl_lexer():
    """The DSL lexer with a stream filter that re-types the assignment
    glyphs.  In some contexts a PythonLexer compound rule consumes
    ``:=`` with plain ``Operator`` before the DSL's prepended rule sees
    it — normalise so the glyphs always style as ``Operator.Word``."""
    from pygments.filter import Filter
    from pygments.token import Operator

    from .Engineer_Style import EngineeringDSLLexer

    class _AssignGlyphs(Filter):
        def filter(self, lexer, stream):
            for ttype, value in stream:
                if value in (":=", "≔", "←") and ttype in Operator:
                    ttype = Operator.Word
                yield ttype, value

    lexer = EngineeringDSLLexer()
    lexer.add_filter(_AssignGlyphs())
    return lexer


def _dsl_highlight_filter():
    """A ``highlight_code`` Jinja filter using the DSL lexer, matching
    the markup shape of nbconvert's stock ``Highlight2HTML``."""
    from pygments import highlight
    from pygments.formatters import HtmlFormatter

    lexer = _dsl_lexer()

    def highlight_code(source, language=None, metadata=None):
        formatter = HtmlFormatter(cssclass=f"highlight hl-{language or 'ipython3'}")
        return highlight(source, lexer, formatter)

    return highlight_code


def _mathstyle_class(ttype):
    """Map a Pygments token type to a math-typography CSS class.

    The DSL lexer is used for *structure* only; instead of colours the
    classes carry engineering-document typography — variables italic,
    units/functions/numbers upright, comments grey.  Order matters:
    units and Greek letters both live under ``Name.Builtin``.
    """
    from pygments.token import Comment, Keyword, Name, Operator, String

    if ttype in Comment:
        return "edsl-comment"
    if ttype in String:
        return "edsl-str"
    if ttype in Keyword or ttype is Operator.Word:      # for/if/… and := ≔ ←
        return "edsl-kw"
    if ttype is Name.Builtin.Unit:                      # V, Ω, kΩ — upright
        return "edsl-up"
    if ttype is Name.Builtin.Greek:                     # π, α, ω — variables
        return "edsl-var"
    if ttype is Name.Constant.Physical:                 # c, h, ε_0 — italic
        return "edsl-var"
    if ttype in Name.Function or ttype in Name.Builtin or ttype in Name.Class \
            or ttype in Name.Namespace or ttype in Name.Decorator:
        return "edsl-up"                                # pp, plot, parallel …
    if ttype in Name:                                   # ordinary variables
        return "edsl-var"
    return ""


def _subscripted(escaped):
    """``R_total`` → ``R<sub>total</sub>`` (input is already HTML-escaped;
    dunder-ish and leading-underscore names pass through untouched)."""
    base, sep, sub = escaped.partition("_")
    if sep and base and sub:
        return f"{base}<sub>{sub}</sub>"
    return escaped


def _mathstyle_filter():
    """``highlight_code`` filter typesetting DSL source as monochrome
    engineering notation rather than coloured code."""
    import html as html_mod

    from pygments import lex

    lexer = _dsl_lexer()

    def highlight_code(source, language=None, metadata=None):
        parts = []
        for ttype, value in lex(source, lexer):
            text = html_mod.escape(value)
            cls = _mathstyle_class(ttype)
            if cls == "edsl-var":
                text = _subscripted(text)
            parts.append(f'<span class="{cls}">{text}</span>' if cls else text)
        return ('<div class="highlight edsl-math"><pre>'
                + "".join(parts) + "</pre></div>")

    return highlight_code


def _dsl_pygments_css():
    """CSS for the DSL colour scheme (``Engineer_Style``) with every
    bold weight stripped — the JupyterLab editor look, print-calibrated:
    bold code prints heavy and uneven on paper."""
    from pygments.formatters import HtmlFormatter

    from .Engineer_Style import EngineeringDSLStyle

    css = HtmlFormatter(style=EngineeringDSLStyle).get_style_defs(".highlight")
    css = css.replace("font-weight: bold", "font-weight: normal")
    # The Lab theme sheet bolds several token classes (.o, .k, .ow …)
    # that this style doesn't always redefine — the blanket rule wins
    # over any of them, so no code prints bold anywhere.
    return css + "\n.highlight span { font-weight: normal !important; }"


def _edsl_clean_html():
    """The stock ``clean_html`` sanitizer with ``sub``/``sup`` allowed —
    mirrors ``nbconvert.filters.strings.clean_html`` otherwise."""
    import bleach

    from nbconvert.filters.strings import _get_default_css_sanitizer

    kwargs = {}
    css_sanitizer = _get_default_css_sanitizer()
    if css_sanitizer:
        kwargs["css_sanitizer"] = css_sanitizer
    tags = [*bleach.ALLOWED_TAGS, "div", "pre", "code", "span",
            "table", "tr", "td", "sub", "sup"]
    attributes = {**bleach.ALLOWED_ATTRIBUTES, "*": ["class", "id"]}

    def clean_html(element):
        element = element.decode() if isinstance(element, bytes) else str(element)
        return bleach.clean(element, tags=tags, attributes=attributes, **kwargs)

    return clean_html


class EDSLPrintExporter(HTMLExporter):
    """HTML rendering tuned for engineering-DSL hardcopies."""

    export_from_notebook = "EDSL print view"

    code_style = Enum(
        ("color", "math", "stock"),
        default_value="color",
        config=True,
        help="How code cells are typeset: 'color' (the DSL's Pygments "
             "colours, matching the JupyterLab editor scheme, with bold "
             "stripped for print), 'math' (monochrome engineering "
             "typography — variables italic with real subscripts, units "
             "upright, comments grey), or 'stock' (nbconvert's default "
             "highlighting).",
    )

    @default("template_name")
    def _template_name_default(self):
        return "edsl-print"

    @default("extra_template_basedirs")
    def _extra_template_basedirs_default(self):
        return [TEMPLATES_DIR]

    @property
    def default_config(self):
        c = Config({
            "TagRemovePreprocessor": {
                "enabled": True,
                "remove_cell_tags": ["no-print"],
            },
        })
        if super().default_config:
            c2 = super().default_config.copy()
            c2.merge(c)
            c = c2
        return c

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ``HTMLExporter.from_notebook_node`` re-registers highlight_code
        # at export time, consulting ONLY the ``filters`` config trait —
        # anything placed in the Jinja environment earlier (via
        # register_filter or default_filters) is clobbered there.  So the
        # trait is the one override point that sticks.  A user-configured
        # highlight_code still wins: we only fill the gap.
        if "highlight_code" not in self.filters:
            flt = None
            try:
                if self.code_style == "math":
                    flt = _mathstyle_filter()
                elif self.code_style == "color":
                    flt = _dsl_highlight_filter()
            except Exception:        # pragma: no cover - pygments missing
                flt = None           # fall back to nbconvert's stock filter
            if flt is not None:
                self.filters = {**self.filters, "highlight_code": flt}

    def default_filters(self):
        """Extends ``clean_html``'s sanitizer with sub/sup.

        The stock ``clean_html`` (bleach with a fixed tag list) escapes
        the ``<sub>`` markup the math-style highlighter emits for
        subscripted identifiers; this variant keeps the same
        sanitization with ``sub``/``sup`` added to the allowed tags.
        """
        yield from super().default_filters()
        try:
            yield ("clean_html", _edsl_clean_html())
        except Exception:            # pragma: no cover - bleach missing
            pass                     # keep nbconvert's stock sanitizer

    # Render math with MathJax's SVG output instead of CommonHTML.
    # Chromium's print-to-PDF re-layout collapses CommonHTML's *assembled*
    # stretchy delimiters (matrix brackets print as tiny "[ ]"), while SVG
    # math is vector-perfect on paper — so the PDF exporter turns this on.
    # It stays off for the screen view: the jupyter-server-mathjax bundle
    # that serves the print view offline does not ship the SVG font data.
    svg_math = False

    def from_notebook_node(self, nb, resources=None, **kwargs):
        resources = self._init_resources(resources)
        resources["edsl_svg_math"] = bool(self.svg_math)
        resources["edsl_pygments_css"] = ""
        if self.code_style == "color":
            try:
                resources["edsl_pygments_css"] = _dsl_pygments_css()
            except Exception:        # pragma: no cover - pygments missing
                pass
        return super().from_notebook_node(nb, resources=resources, **kwargs)


class EDSLPDFExporter(EDSLPrintExporter, WebPDFExporter):
    """The same rendering, printed to PDF through Chromium (webpdf)."""

    export_from_notebook = "EDSL PDF"
    svg_math = True

    def run_playwright(self, html):
        """Windows: give Playwright an event loop that can spawn Chromium.

        ``WebPDFExporter.run_playwright`` calls ``asyncio.run`` in a
        worker thread, which builds its loop from the *global* policy.
        Both the nbconvert CLI and jupyter-server force the Selector
        policy on Windows (their ZMQ/tornado sides need it), and
        Selector loops cannot spawn subprocesses — so a plain export
        dies with ``NotImplementedError`` (in JupyterLab's export menu:
        a 500).  Swap in the Proactor policy just for the PDF build and
        restore the previous policy afterwards; loops that already
        exist (the server's own) are unaffected by a policy swap.
        """
        import asyncio
        import sys as _sys

        if not _sys.platform.startswith("win"):
            return super().run_playwright(html)
        previous = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            return super().run_playwright(html)
        finally:
            asyncio.set_event_loop_policy(previous)
