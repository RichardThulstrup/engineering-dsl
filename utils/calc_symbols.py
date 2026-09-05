from math import pi as _math_pi
import sympy as _sym
import forallpeople as si

# These names need to be in scope because the import-hook source transform
# rewrites every numeric literal in this file as `_S(<literal>, <sf>)`.
from .sigfig import _S, _INF, exact, measured

# assumes your forallpeople environment already exposes these:
# A, Ohm, W, F, H, Hz, s, V, m, Celsius

# ---------------------------------------------------------------------------
# Math constants — sympy by default
# ---------------------------------------------------------------------------
# π, e, i are exposed as their sympy symbolic equivalents so identities
# like Euler's (``e**(i*π) == -1``) auto-simplify.  For numeric arithmetic
# you don't need to do anything special — sympy's ``__float__`` on these
# constants returns the matching ``math.pi`` / ``math.e`` value, so any
# function that calls ``float(x)`` (and most of the toolkit's internals
# do) keeps working without changes.
#
# The interaction with ``forallpeople.Physical`` is handled by a small
# adapter installed in ``Engineer.py``: when sympy tries to multiply a
# Physical, it raises ``SympifyError`` and Python falls back to the
# Physical's ``__rmul__``, which converts the sympy expression to a
# float and produces a unit-bearing result.  So ``2π * (50 Hz)`` gives
# ``314.159 Hz`` (units kept), losing the symbolic structure of ``2π``
# in that mixed expression — that's the documented trade-off.
#
# The DSL's degree rewrite (``5°``) deliberately uses ``math.pi`` rather
# than ``π`` so degrees stay numeric for plot axes etc.  See
# ``rewrite_degrees`` in ``circuit_dsl.py``.

# ---------------------------------------------------------------------------
# Prefix scalars
# ---------------------------------------------------------------------------
# Each factor is wrapped with `exact(...)` to mark it as a *definition*, not
# a measurement, so the prefix doesn't drag down the precision of any
# quantity it multiplies.  Without this, writing `1e-3` instead of `1/1000`
# in any future edit would silently make the prefix 1 sf and any measurement
# using it would get clipped to 1 sf.
#
# Bare integer literals (the `1000`s here) are *already* exact by the
# literal-parsing rule, so `1/1000` is exact even without `exact(...)`.
# The wrap is defensive: it makes the intent explicit and survives
# refactoring to float-literal forms.

prefix_p := exact(1/1000_000_000_000)
prefix_n := exact(1/1000_000_000)
prefix_μ := exact(1/1000_000)
prefix_d := exact(1/10)
prefix_c := exact(1/100)
prefix_m := exact(1/1000)
prefix_k := exact(1000)
prefix_M := exact(1000_000)
prefix_G := exact(1000_000_000)
prefix_T := exact(1000_000_000_000)

# ---------------------------------------------------------------------------
# Unit prefixes
# ---------------------------------------------------------------------------
# No literals on these lines — each is a product of an already-exact prefix
# scalar with a forallpeople unit, so the result is exact by construction
# and any measurement multiplied by one of these inherits its own sf cleanly.

pA := prefix_p*A
nA := prefix_n*A
μA := prefix_μ*A
mA := prefix_m*A

mΩ := prefix_m*Ohm
Ω := Ohm
kΩ := prefix_k*Ohm
MΩ := prefix_M*Ohm
GΩ := prefix_G*Ohm

nW := prefix_n*W
mW := prefix_m*W

ptm := prefix_m
ptc := prefix_d
ppm := prefix_μ

pF := prefix_p*F
nF := prefix_n*F
μF := prefix_μ*F
mF := prefix_m*F

pH := prefix_p*H
nH := prefix_n*H
μH := prefix_μ*H
mH := prefix_m*H

kHz := prefix_k*Hz
MHz := prefix_M*Hz
GHz := prefix_G*Hz
THz := prefix_T*Hz

ps := prefix_p*s
ns := prefix_n*s
μs := prefix_μ*s

pV := prefix_p*V
nV := prefix_n*V
μV := prefix_μ*V
mV := prefix_m*V

# Math constants are sympy symbolic.  ``float(π) == math.pi`` and
# ``complex(i) == 1j`` — coercion through ``__float__``/``__complex__``
# is automatic, so existing numeric code that calls ``float(...)`` on
# these values continues to work transparently.  See the long block at
# the top of this module for the design rationale.
π := _sym.pi
pi := _sym.pi              # spelt-out alias, same object
i := _sym.I                # imaginary unit
e := _sym.E                # Euler's number

mm := prefix_m*m
cm := prefix_c*m
μm := prefix_μ*m
nm := prefix_n*m
pm := prefix_p*m
ms := prefix_m*s
degC := Celsius

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
# Defined-exact values follow the post-2019 SI redefinition.  Everything
# else is wrapped with `measured(value, n)` to carry its CODATA precision.

c    := exact(299_792_458) * m/s                  # speed of light (defined)
h    := exact(6.626_070_15e-34) * J*s             # Planck (defined)
ℏ    := h / (2*_math_pi)                          # reduced Planck (derived, exact)
k_B  := exact(1.380_649e-23) * J/K                # Boltzmann (defined)
N_A  := exact(6.022_140_76e23) / mol              # Avogadro (defined)
q_e  := exact(1.602_176_634e-19) * C              # elementary charge (defined)
R_gas:= N_A * k_B                                  # gas constant (derived, exact)
g_n  := exact(9.806_65) * m/s/s                   # standard gravity (defined)
T_0  := exact(273.15) * K                          # ice point (defined)

# CODATA-measured constants (sf reflects published precision, ~10–12 digits).
ε_0  := measured(8.854_187_812_8e-12, 10) * F/m   # vacuum permittivity
μ_0  := measured(1.256_637_062_12e-6, 12) * N/A/A # vacuum permeability
m_e  := measured(9.109_383_701_5e-31, 11) * kg    # electron rest mass
m_p  := measured(1.672_621_923_69e-27, 12) * kg   # proton rest mass
m_n  := measured(1.674_927_498_04e-27, 12) * kg    # neutron rest mass
G    := measured(6.674_30e-11, 5) * m*m*m/kg/s/s   # Newtonian gravitation
σ_SB := measured(5.670_374_419e-8, 10) * W/m/m/K/K/K/K   # Stefan–Boltzmann (exact in SI 2019; 10 digits kept)
Faraday := N_A * q_e                               # Faraday constant (derived, exact) — ``F`` is the farad
F_c  := Faraday
α_fs := measured(7.297_352_5693e-3, 11)           # fine-structure constant (dimensionless)
alpha_fs := α_fs
a_0  := measured(5.291_772_109_03e-11, 12) * m     # Bohr radius
μ_B  := measured(9.274_010_0783e-24, 11) * J/T     # Bohr magneton
Z_0  := measured(376.730_313_668, 12) * Ω          # impedance of free space
R_inf := measured(10_973_731.568_160, 14) / m      # Rydberg constant (write R_∞ in source)
b_wien := measured(2.897_771_955e-3, 10) * m*K     # Wien displacement constant
p_0  := exact(101_325) * Pa                        # standard atmosphere

# Subscript/superscript-bearing aliases — same physical constants under
# the visually-richer notation that engineers often prefer.  Note that
# Python's PEP 3131 normalizes all identifiers to NFKC form at parse
# time: when a user types ``εₒ``, the parser converts it to ``εo`` (the
# subscript "o" becomes a plain "o").  So the *attribute names* below
# use the NFKC-normalized form — ``εo``, ``μo``, ``NA`` — even though
# the user sees and types the decorated form ``εₒ``, ``μₒ``, ``Nᴬ``.
# Python handles the conversion transparently.
#
# A separate concern is that the toolkit's subscript-as-index rewriter
# (``rewrite_subscript_indices``) operates on raw source text BEFORE
# Python parses it, and would otherwise turn ``εₒ`` into ``ε[o]``.
# A dedicated pass in ``circuit_dsl.transform_source`` (the
# ``_protect_constant_names`` helper) stashes these whole-name
# identifiers before the rewriters run and restores them after.  Both
# pieces are needed: protection to escape the DSL rewriter, and
# NFKC-aware storage to play nicely with Python's identifier model.
εo   := ε_0   # vacuum permittivity, subscript-o variant (εₒ in source)
μo   := μ_0   # vacuum permeability (μₒ in source)
me   := m_e   # electron rest mass (mₑ in source)
mp   := m_p   # proton rest mass (mₚ in source)
kβ   := k_B   # Boltzmann (kᵦ in source; ᵦ normalizes to β not B)
qe   := q_e   # elementary charge (qₑ in source)
Rgas := R_gas # gas constant (Rᵍᵃˢ in source; using U+02E2 modifier-s)
NA   := N_A   # Avogadro (Nᴬ in source)
gn   := g_n   # standard gravity (gₙ in source) — "g sub n", textbook form
go   := g_n   # standard gravity (gₒ in source) — "g sub zero", modern IUPAC
              # form. Same value as gn; both aliases exist because users
              # variously type ``gₙ`` (textbook) or ``gₒ`` (matching the
              # ``g_0`` underscore-zero spelling).
To   := T_0   # ice-point reference (Tₒ in source)

# An exactly-infinite scalar (useful for ideal op-amp inputs, unbounded
# ranges, etc.).  Use either the symbol ``∞`` or the ASCII alias ``inf`` —
# the source-transform rewrites ``∞`` to ``inf`` before parsing.
inf  := exact(float("inf"))

__all__ := [
    "prefix_p", "prefix_n", "prefix_μ", "prefix_d", "prefix_c",
    "prefix_m", "prefix_k", "prefix_M", "prefix_G", "prefix_T",
    "pA", "nA", "μA", "mA",
    "mΩ", "Ω", "kΩ", "MΩ", "GΩ",
    "nW", "mW",
    "ptm", "ptc", "ppm",
    "pF", "nF", "μF", "mF",
    "pH", "nH", "μH", "mH",
    "kHz", "MHz", "GHz", "THz", "ps", "ns", "μs",
    "pV", "nV", "μV", "mV",
    "π", "pi", "e", "mm", "cm", "μm", "nm", "pm", "ms", "degC", "i",
    # Physical constants:
    "c", "h", "ħ", "k_B", "N_A", "q_e", "R_gas", "g_n", "T_0",
    "ε_0", "μ_0", "m_e", "m_p", "m_n",
    "G", "σ_SB", "Faraday", "F_c", "α_fs", "alpha_fs", "a_0", "μ_B",
    "Z_0", "R_inf", "b_wien", "p_0",
    # Same constants under subscript/superscript-bearing aliases.  The
    # names listed here are the NFKC-NORMALIZED forms — Python parses
    # ``εₒ`` (the visual form) into the identifier ``εo``, so the
    # module attribute is named ``εo``.  Users type ``εₒ``; Python
    # silently maps it to ``εo``; lookup succeeds.
    "εo", "μo", "me", "mp", "kβ", "qe", "Rgas", "NA", "gn", "go", "To",
    "inf",
]


# ---------------------------------------------------------------------------
# Auto-enable identifier protection in STRICT mode.
#
# Strict mode protects single-letter SI unit names (``V``, ``A``, ``F``,
# ``H``, ``C``, ``T``, ``K``, ``m``, ``s``, ``W``, ``J``, ``N``, ``S``,
# ``Ω``) on top of the usual prefix/derived units and constants.  These
# are commonly desired as engineering variable names (force ``F``, mass
# ``m``, temperature ``T``, capacitance ``C``, …) which is why we used
# to leave them un-protected — but silently overwriting a unit gives
# very confusing later errors ("why is ``2 * N`` saying float × float
# = wrong type?") because the variable shadows the unit module-wide
# and nothing tells you it happened.  Strict-default trades that
# silent failure for a loud error at the assignment site, which is
# almost always what an engineer wants.
#
# To opt out:
#
#     import utils.circuit_dsl as dsl
#     dsl.clear_protections()              # release everything
#     dsl.unprotect("F", "m", "g")         # release a few
#     dsl.protect_si_units(strict=False)   # go back to non-strict
#
# Or use names that don't collide: ``Force``, ``mass``, ``gravity``.
from . import circuit_dsl as _dsl
_dsl.protect_all(strict=True)
