"""Extra unit definitions: imperial, pressure, energy, force, volume.

Each name in this module is a ``forallpeople.Physical`` instance — the same
type as ``m``, ``s``, ``kg`` from forallpeople's default environment.  That
means they participate naturally in the toolkit:

  - Multiply by a number to get a measurement: ``5 * inch`` → 5.000 inch
  - Use with the DSL's ``▶`` operator: ``length ▶ inch`` → display in inches
  - Mix with SI: ``5 inch + 2 cm`` works because both reduce to metres

The Physicals are constructed by scaling the SI base units, so they carry
the right dimensions automatically.  forallpeople reduces results to base
SI on display; two families override that with a written-form-preserving
display tag (see the torque section): the torque units (``5 Nm`` stays
``N·m``, never ``J``) and the imperial force/length units (``5 inch``
stays ``inch``, ``20 ozf inch`` composes to ``ozf·inch``).  Everything
else reduces as before — that's where the ``▶`` operator earns its keep,
re-expressing a result in the unit you want to read.

Conversions are exact where the official definition is exact (the inch was
redefined in 1959 as exactly 25.4 mm), and the most precise published value
otherwise (psi, atm, mmHg).  All literals use ``exact(...)`` so they don't
inherit a sigfig limit from their decimal representation.

To use this module, importing ``Engineer.py`` brings the whole set in via
``from .extra_units import *``.  Or import individually::

    from utils.extra_units import inch, ft, psi, bar
"""

from .sigfig import exact
import forallpeople as _si_internal
from forallpeople import (
    m as _m, s as _s, kg as _kg, A as _A, K as _K, mol as _mol, cd as _cd,
)


# ---------------------------------------------------------------------------
# Length — imperial
# ---------------------------------------------------------------------------
# 1959 international yard/pound agreement: inch is defined as exactly 25.4
# mm.  Every other imperial length unit derives from the inch by exact
# integer ratios, so all of these are exact.
inch    = exact(0.0254)         * _m            # 25.4 mm  (definition)
ft      = exact(12)             * inch          # 0.3048 m
yard    = exact(3)              * ft            # 0.9144 m
mile    = exact(5280)           * ft            # 1609.344 m
nautical_mile = exact(1852)     * _m            # international (definition)
thou    = exact(0.001)          * inch          # = mil = 0.0254 mm
mil     = thou                                  # alias
parsec  = exact(3.085_677_581_491_3673e16) * _m # IAU 2015
pc      = parsec                                # canonical short form
ly      = exact(9.460_730_472_580_8e15)    * _m # light-year (Julian)
au      = exact(149_597_870_700)            * _m # astronomical unit

# Extragalactic distance scales.  Galaxy separations are tens of Mpc,
# observable-universe radius is on order of Gpc.  Standard prefixes on
# the parsec — no astronomer ever writes ``1e22 m``.
kpc     = exact(1000)           * parsec        # kiloparsec — galactic disk scale
Mpc     = exact(1e6)            * parsec        # megaparsec — galaxy-cluster scale
Gpc     = exact(1e9)            * parsec        # gigaparsec — Hubble distance ≈ 4 Gpc


# ---------------------------------------------------------------------------
# Length — metric prefixes (the calc_symbols module ships mm/cm/μm/nm/pm
# but not the larger or unusual prefixes; complete the set here so users
# can write ``254000 cm ▶ km`` without surprise)
# ---------------------------------------------------------------------------
km      = exact(1000)           * _m            # kilometre
hm      = exact(100)            * _m            # hectometre (rare)
dam     = exact(10)             * _m            # decametre (rare)
dm      = exact(0.1)            * _m            # decimetre
fm      = exact(1e-15)          * _m            # femtometre (particle physics)
Å       = exact(1e-10)          * _m            # ångström (atomic scale)


# ---------------------------------------------------------------------------
# Time — metric prefixes (calc_symbols has ms and μs)
# ---------------------------------------------------------------------------
ns      = exact(1e-9)           * _s            # nanosecond
ps      = exact(1e-12)          * _s            # picosecond
fs      = exact(1e-15)          * _s            # femtosecond
ks      = exact(1000)           * _s            # kilosecond (~16.7 min)


# ---------------------------------------------------------------------------
# Mass — kg is the base SI unit which makes prefixing slightly odd
# ---------------------------------------------------------------------------
# kg is already defined; here we provide g, mg, etc. on top of it.  Note
# the off-by-one feel: ``g = kg / 1000`` because gram is one prefix step
# DOWN from the base, unlike every other SI unit.
g       = exact(1e-3)           * _kg           # gram
mg      = exact(1e-6)           * _kg           # milligram
μg      = exact(1e-9)           * _kg           # microgram (medicine doses)
ng      = exact(1e-12)          * _kg           # nanogram


# ---------------------------------------------------------------------------
# Frequency — Hz prefixes (RF/electronics workhorses)
# ---------------------------------------------------------------------------
# forallpeople's default environment includes ``Hz`` (= 1/s).  We extend
# the prefixed set here.  Use ``Hz_unit`` internally to avoid colliding
# with the user's potential variable names.
_Hz_unit = exact(1) / _s
Hz      = _Hz_unit
kHz     = exact(1e3)            * _Hz_unit
MHz     = exact(1e6)            * _Hz_unit
GHz     = exact(1e9)            * _Hz_unit
THz     = exact(1e12)           * _Hz_unit


# ---------------------------------------------------------------------------
# Voltage / current — large prefixes (calc_symbols has μV/μA, mV/mA via
# the prefix_m and prefix_μ patterns)
# ---------------------------------------------------------------------------
# Build V and A from base SI units rather than importing them — forallpeople
# exposes them only via ``environment(top_level=True)`` (injected into
# builtins), not as importable module attributes.
# V = kg·m²·s⁻³·A⁻¹  (after expressing A in base units, but here we leave A
# as a base since forallpeople's environment provides it; we just construct
# V dimensionally as kg·m²/(s³·A) where the inputs are the base SI units
# from forallpeople imported above as _kg, _m, _s, _A).
_V_unit = _kg * _m * _m / (_s * _s * _s * _A)
kV      = exact(1e3)            * _V_unit
MV      = exact(1e6)            * _V_unit
mA      = exact(1e-3)           * _A
kA      = exact(1e3)            * _A


# ---------------------------------------------------------------------------
# Mass — imperial / avoirdupois
# ---------------------------------------------------------------------------
# Pound is exactly 0.453_592_37 kg by the 1959 agreement.  Other avoirdupois
# units derive from it by exact integer ratios.  Troy units (used for
# precious metals) have a different ounce; both are included.
lb      = exact(0.453_592_37)   * _kg           # avoirdupois pound
lbm     = lb                                    # alias for "pound mass"
oz      = lb / exact(16)                        # avoirdupois ounce
grain   = lb / exact(7000)                      # used in ammunition
slug    = exact(14.593_902_937_206_362) * _kg   # gravitational mass unit
stone   = exact(14)             * lb            # 6.350... kg, UK use
ton_us  = exact(2000)           * lb            # short ton
ton_uk  = exact(2240)           * lb            # long ton
tonne   = exact(1000)           * _kg           # metric ton
ozt     = exact(31.103_476_8e-3) * _kg          # troy ounce (precious metals)

# Astronomical mass units.  Stars, planets, galaxies all live at the
# ``10²⁹`` to ``10⁴³`` kg scale — well past the yotta prefix and into
# the new ronna / quetta range that almost nobody actually uses in
# papers.  The convention is to give masses in multiples of the Sun
# (M_sun, written ``M☉`` in journals), Earth, or Jupiter masses;
# galactic-scale masses get reported as ``10¹⁰ M_sun`` rather than
# ``2×10⁴⁰ kg``.  IAU 2015 nominal values where applicable.
M_sun     = exact(1.988_416e30) * _kg           # solar mass (nominal, IAU 2015)
M_earth   = exact(5.972_2e24)   * _kg           # Earth mass
M_jupiter = exact(1.898_19e27)  * _kg           # Jupiter mass
M_moon    = exact(7.342e22)     * _kg           # Lunar mass

# Astronomical radii.  Solar radius (R☉) is the natural unit for
# stellar dimensions — stars range over roughly 0.1 to 1000 R☉.
# Earth radius (R⊕) likewise dominates planetary work (exoplanet
# discoveries report radii in R⊕).  IAU 2015 nominal values.
R_sun     = exact(6.957e8)      * _m            # solar equatorial radius
R_earth   = exact(6.378_1e6)    * _m            # Earth equatorial radius
R_jupiter = exact(7.149_2e7)    * _m            # Jupiter equatorial radius

# Solar luminosity — every stellar evolution paper reports L in L☉.
# Defined via IAU 2015 as ``L⊙ = 3.828 × 10²⁶ W`` (the nominal value;
# the actual Sun's luminosity has small time-variation).  Range: stars
# from ~10⁻⁴ L☉ (red dwarfs) to ~10⁶ L☉ (luminous blue variables).
# Re-derived from base units (W = kg·m²/s³) rather than importing the
# public ``W`` defined later in this file — keeps the astro block
# self-contained.
L_sun     = exact(3.828e26) * _kg * _m**2 / _s**3

# Solar effective temperature — the IAU-defined nominal value, used
# as a reference in stellar atmosphere work.
T_sun     = exact(5772)         * _K            # effective temperature


# ---------------------------------------------------------------------------
# Force
# ---------------------------------------------------------------------------
# Standard gravity is defined as exactly 9.80665 m/s² (CGPM 1901).
# Exposed as both ``g_0`` (the modern IUPAC / metrology spelling — "g
# sub-zero", the standard reference acceleration) and ``g_n`` (the older
# but still common "standard gravity" symbol from physics textbooks).
# Internal ``_g_n`` remains for the few places below that derived from
# it before this module exposed the public name.
_g_n    = exact(9.806_65) * _m / _s / _s
g_0     = _g_n                                  # IUPAC / metrology spelling
g_n     = _g_n                                  # textbook spelling

N       = _kg * _m / _s / _s                    # newton — base derived
# Spelled-out aliases for the SI derived units whose single-letter names
# collide with common sympy imports.  ``sympy.N`` is a numeric-evaluator
# function, ``sympy.C`` is a polynomial-coefficient class, ``sympy.O`` is
# the big-O symbol — so a user doing ``from sympy import *`` after the
# toolkit imports gets ``N`` etc. shadowed by sympy's versions and
# loses access to the unit.  The spelled-out names below survive any
# such shadowing because sympy doesn't have ``newton``, ``coulomb``,
# ``farad``, ``ampere``, ``henry``, ``volt``, ``watt`` defined.
newton  = N
# Force prefixes — engineering frequently spans 6+ orders of magnitude
# (from μN strain-gauge sensitivities through kN structural loads to
# MN rocket thrust), so completing the prefix family makes typical
# force calculations read naturally without manual scaling.
μN      = exact(1e-6)           * N             # micronewtons
mN      = exact(1e-3)           * N             # millinewtons
kN      = exact(1e3)            * N             # kilonewtons
MN      = exact(1e6)            * N             # meganewtons
GN      = exact(1e9)            * N             # giganewtons

lbf     = lb  * _g_n                            # pound-force = lb × g
ozf     = lbf / exact(16)                       # ounce-force = lbf/16
kgf     = _kg * _g_n                            # kilogram-force (legacy)
dyne    = exact(1e-5)           * N             # CGS unit


# ---------------------------------------------------------------------------
# Electric charge — coulomb and prefixes
# ---------------------------------------------------------------------------
# Coulomb is derived from the base ampere and second: C = A·s.  Common
# prefix range covers semiconductor charge measurements (pC, nC) through
# capacitor charging (mC, μC) to industrial-scale charge (kC).
C       = _A * _s                               # coulomb — base derived
coulomb = C                                     # spelled-out alias — see ``newton``
                                                # for the shadowing rationale
μC      = exact(1e-6)           * C             # microcoulombs
mC      = exact(1e-3)           * C             # millicoulombs
nC      = exact(1e-9)           * C             # nanocoulombs
pC      = exact(1e-12)          * C             # picocoulombs
kC      = exact(1e3)            * C             # kilocoulombs


# ---------------------------------------------------------------------------
# Capacitance — farad and prefixes
# ---------------------------------------------------------------------------
# Farad is enormous as a base unit; real capacitors are typically pF, nF,
# or μF.  ``μF`` is already defined by ``calc_symbols.py``; pF/nF/mF are
# added here so the full common-engineering range is available.
F_unit  = C / (_kg * _m * _m / (_s * _s * _s * _A))  # = C / V (capacitance)
# Equivalently, F = s⁴·A²/(kg·m²), but writing it through V keeps the
# physical meaning visible: capacitance = charge per volt.
pF      = exact(1e-12)          * F_unit        # picofarads
nF      = exact(1e-9)           * F_unit        # nanofarads
mF      = exact(1e-3)           * F_unit        # millifarads
# μF intentionally not redefined here — calc_symbols ships it, and a
# second definition with a different construction path (``F_unit``-based
# vs. ``prefix_μ*F``-based) could create two distinct Physical objects
# that happen to be numerically equal.  Keeping one canonical μF avoids
# subtle identity-vs-equality bugs.


# ---------------------------------------------------------------------------
# Pressure
# ---------------------------------------------------------------------------
# Pa is the SI base.  bar, mbar, atm, mmHg, torr, and psi cover almost every
# pressure measurement an engineer encounters.  mmHg and torr are technically
# slightly different historically but agree to 7+ digits — both pinned to
# the modern definition (133.322_387_415 Pa).
Pa      = N / (_m * _m)                         # base
hPa     = exact(100)            * Pa            # hectopascal — meteorology
kPa     = exact(1000)           * Pa
MPa     = exact(1_000_000)      * Pa
GPa     = exact(1_000_000_000)  * Pa
bar     = exact(100_000)        * Pa            # exact by definition
mbar    = exact(100)            * Pa            # = hPa, common in meteorology
atm     = exact(101_325)        * Pa            # standard atmosphere (1954)
torr    = atm / exact(760)                      # 1 atm / 760 (exact ratio)
mmHg    = exact(133.322_387_415) * Pa           # CIPM 2007 definition
psi     = lbf / (inch * inch)                   # pound-force per square inch
ksi     = exact(1000)           * psi
inH2O   = exact(249.088_908_333) * Pa           # inch of water at 60°F


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------
# J is base.  cal (thermochemical, used in chemistry) and Cal (kilocalorie,
# used on food labels) are distinguished by capitalisation — a notational
# convention that's confused generations of engineers.
J       = N * _m                                # joule — base derived
kJ      = exact(1000)           * J
MJ      = exact(1_000_000)      * J
GJ      = exact(1_000_000_000)  * J
cal     = exact(4.184)          * J             # thermochemical calorie
Cal     = exact(4184)           * J             # food calorie = kcal
kcal    = Cal                                   # alias
BTU     = exact(1055.055_852_62) * J            # international table BTU
kWh     = exact(3_600_000)      * J             # kilowatt-hour
Wh      = exact(3600)           * J             # watt-hour
eV      = exact(1.602_176_634e-19) * J          # electron-volt (defined)
keV     = exact(1000)           * eV
MeV     = exact(1_000_000)      * eV
GeV     = exact(1_000_000_000)  * eV
TeV     = exact(1e12)           * eV            # tera-eV — LHC scale
PeV     = exact(1e15)           * eV            # peta-eV — Tevatron-and-above
EeV     = exact(1e18)           * eV            # exa-eV — UHECR (cosmic-ray) scale
erg     = exact(1e-7)           * J             # CGS unit

# Mass in particle-physics convention: ``eV/c²``.  By Einstein, a mass
# of one ``eV/c²`` is the mass whose rest energy is one electron-volt;
# numerically that's ``1 eV / c² = 1.783e-36 kg``.  These units make
# the electron mass ``0.511 MeV/c²`` and the proton ``938 MeV/c²`` —
# the standard particle-physics presentation, far cleaner than the
# bare-kg form (9.109e-31 kg) for sub-atomic work.
#
# Implementation: we store these as Physical quantities with dimension
# of mass.  The conversion factor is ``eV / c²`` where ``c`` is the
# speed of light — both forallpeople scalars, so the result is a pure
# mass.  This means the user can write ``mₑ ▸ MeV_per_c²`` to display
# the electron mass in MeV/c² without any custom conversion code.
_c_speed = exact(299_792_458) * _m / _s         # c, for the m_eV/c² scale
eV_per_c2     = eV  / _c_speed**2               # ≈ 1.783e-36 kg
keV_per_c2    = keV / _c_speed**2
MeV_per_c2    = MeV / _c_speed**2
GeV_per_c2    = GeV / _c_speed**2
TeV_per_c2    = TeV / _c_speed**2

# Momentum units, ``eV/c`` family.  By the same Einstein logic that
# makes mass naturally expressible in eV/c², momentum is naturally
# expressible in eV/c — track momenta in collider experiments and
# Compton wavelengths in atomic physics both use this.  Numerically
# ``1 eV/c = 5.344e-28 kg·m/s``.
eV_per_c      = eV  / _c_speed
keV_per_c     = keV / _c_speed
MeV_per_c     = MeV / _c_speed
GeV_per_c     = GeV / _c_speed
TeV_per_c     = TeV / _c_speed

# Atomic mass unit (Dalton) — defined as 1/12 of the mass of an unbound
# carbon-12 atom at rest.  Used in chemistry and biochemistry.  ``u``
# and ``Da`` are aliases.
u             = exact(1.660_539_066_60e-27) * _kg
Da            = u
amu           = u                               # American/older spelling


# ---------------------------------------------------------------------------
# Torque — display-preserving newton-metre family
# ---------------------------------------------------------------------------
# Torque and energy share the dimension kg·m²/s², so forallpeople's
# display reduction shows a torque as joules: ``5 N*m`` prints
# ``5.000 J``.  Dimensionally defensible, physically misleading — a
# bending moment is not an energy.  No unit system can TELL a torque
# from an energy by dimensions alone, so the fix is notational: write
# the quantity with a torque unit below and the written form is
# preserved on display.
#
# ``_DisplayUnit`` is the marker that does this.  Like ``_DeltaUnit``
# it routes by operator:
#
#   * LITERAL MULTIPLIER — ``5 Nm`` (transform: ``_S(5) * Nm``) → a
#     genuine kg·m²/s² ``Sig``-wrapped Physical carrying a
#     ``_unit_pref`` display tag.  Unlike ``▶``'s edge-only
#     preference, the tag PERSISTS through arithmetic (``2 * M`` is
#     still ``N·m``) — safe because the formatter re-checks dimensions
#     at render time and silently drops the tag the moment the value
#     stops being a torque (``M / t`` shows ``W``; ``M · L`` falls
#     back to reduced SI).  The one physically-undetectable case:
#     torque × dimensionless angle is an energy but keeps showing
#     ``N·m`` — annotate with ``▶ J`` there.
#   * ``▶`` TARGET — ``M ▶ kNm`` → ``in_units`` unwraps the marker to
#     its Physical and uses its canonical label.
#   * UNIT IN AN EXPRESSION — ``τ / Nm``, ``Nm / rad`` → the plain
#     Physical, so unit algebra works as if ``N*m`` had been written.
class _DisplayUnit:
    """Marker for a compound unit that keeps its written form on
    display instead of reducing to the derived SI unit (``N·m`` not
    ``J``).  ``physical`` is the unit's value as a forallpeople
    ``Physical``; ``label`` is the canonical display text."""

    __slots__ = ("physical", "label")

    def __init__(self, physical, label):
        # Strip any ``Sig`` layer (units built with ``exact(...)`` carry
        # one) so ``physical`` is always a bare forallpeople ``Physical``
        # — the display formatter divides by it, and ``Physical / Sig``
        # raises where ``Physical / Physical`` works.
        from .sigfig import _unwrap
        self.physical = _unwrap(physical)
        self.label = label

    # ----- literal-multiplier use: ``n * Nm`` → tagged Sig -----
    # When ``n`` itself already carries a display-unit tag, the two
    # units COMPOSE: ``20 ozf inch`` evaluates left-to-right as
    # ``(20 · ozf) · inch`` — the first product is a force tagged
    # ``ozf``, and multiplying by ``inch`` yields a torque tagged
    # ``ozf·inch``.  The composed tag passes through the same
    # render-time dimension guard as any other, so it can never
    # mislabel a value.
    def __rmul__(self, n):
        from .sigfig import Sig, _sf_of
        npref = getattr(n, "_unit_pref", None)
        q = n * self.physical
        if not isinstance(q, Sig):
            try:
                q = Sig(q, _sf_of(n))
            except Exception:
                # Not Sig-wrappable (e.g. an array product) — behave as
                # the plain unit so the expression still evaluates.
                return q
        pref = self
        if npref is not None:
            try:
                pref = _DisplayUnit(npref.physical * self.physical,
                                    f"{npref.label}·{self.label}")
            except Exception:
                pref = self
        try:
            q._unit_pref = pref
        except Exception:
            pass
        return q

    # ----- additive use keeps plain-unit behaviour (``L - inch``) -----
    def __radd__(self, x):
        return x + self.physical

    def __rsub__(self, x):
        return x - self.physical

    # ----- expression-unit use: plain-Physical behaviour -----
    def __mul__(self, o):
        # Marker × marker COMPOSES into a compound display unit.  The
        # DSL groups adjacent unit names into a parenthesized product,
        # so ``20 ozf inch`` can arrive as ``_S(20) * (ozf * inch)`` —
        # the inner product must stay a display unit (``ozf·inch``),
        # not collapse through forallpeople's float-coercion.
        if isinstance(o, _DisplayUnit):
            return _DisplayUnit(self.physical * o.physical,
                                f"{self.label}·{o.label}")
        return self.physical * o

    def __truediv__(self, o):
        if isinstance(o, _DisplayUnit):
            o = o.physical
        return self.physical / o

    def __rtruediv__(self, x):
        return x / self.physical

    def __pow__(self, p):
        return self.physical ** p

    def __float__(self):
        return float(self.physical)

    def __repr__(self):
        return self.label


Nm     = _DisplayUnit(N * _m, "N·m")                  # newton-metre
mNm    = _DisplayUnit(exact(1e-3) * N * _m, "mN·m")   # small motors, servos
kNm    = _DisplayUnit(exact(1e3)  * N * _m, "kN·m")   # structural moments
MNm    = _DisplayUnit(exact(1e6)  * N * _m, "MN·m")   # heavy civil work
Nmm    = _DisplayUnit(exact(1e-3) * N * _m, "N·mm")   # machine design (= mN·m)
lbf_ft   = _DisplayUnit(lbf * ft,   "lbf·ft")         # imperial torque
lbf_inch = _DisplayUnit(lbf * inch, "lbf·inch")       # fastener specs
# US datasheets write the ounce-force inch as "oz-in"; the force is
# implied by context, but the unit is ounce-FORCE × inch
# (≈ 7.0616 mN·m).  Named ``ozf_inch``: compound unit names are built
# only from names the toolkit itself understands (``ozf``, ``inch``) —
# and ``in`` can never be one, it's a Python keyword.  The two-word
# spelling ``20 ozf inch`` works too, via tag composition above.
ozf_inch = _DisplayUnit(ozf * inch, "ozf·inch")       # small-motor torque


# ---------------------------------------------------------------------------
# Imperial force & length — display-preserving rebind
# ---------------------------------------------------------------------------
# The imperial units themselves get the same written-form-preserving
# treatment: ``5 inch`` displays ``5.000 inch`` (not ``127.0 mm``),
# ``3 lbf`` stays ``3.000 lbf`` (not ``13.34 N``).  More importantly,
# products of tagged units COMPOSE (see ``_DisplayUnit.__rmul__``), so
# the natural torque spelling ``20 ozf inch`` displays ``20.00
# ozf·inch`` — not ``141.2 mJ``.
#
# The rebind happens HERE, after every module-internal use of the
# plain values (``ft = 12·inch``, ``psi = lbf/inch²``, the compound
# torque units above) — those must keep building on bare Physicals.
# ``▶ inch`` etc. keep working: ``in_units`` unwraps the marker, and
# arithmetic like ``5 inch + 2 cm`` still reduces to metres underneath
# (it just displays as ``5.787 inch``).  In additive expressions the
# LEFT operand's unit dominates the display: ``5 inch + 2 mm`` reads
# in inches, ``2 mm + 5 inch`` stays SI.
inch = _DisplayUnit(inch, "inch")
ft   = _DisplayUnit(ft,   "ft")
lbf  = _DisplayUnit(lbf,  "lbf")
ozf  = _DisplayUnit(ozf,  "ozf")


# ---------------------------------------------------------------------------
# Particle physics — cross sections
# ---------------------------------------------------------------------------
# The barn is the standard unit of cross section for high-energy
# physics.  ``1 barn = 10⁻²⁸ m²`` — the order of magnitude of nuclear
# geometric cross sections (whence the joke that hitting one is "as
# easy as hitting the broad side of a barn").  Modern collider physics
# works at femtobarn (fb) scale; the LHC has delivered hundreds of
# inverse-femtobarn integrated luminosities, attobarn for searches at
# the limit of statistics.
barn    = exact(1e-28)          * _m * _m       # 10⁻²⁴ cm² = 100 fm²
mbarn   = exact(1e-3)           * barn          # millibarn
μbarn   = exact(1e-6)           * barn          # microbarn
nbarn   = exact(1e-9)           * barn          # nanobarn
pbarn   = exact(1e-12)          * barn          # picobarn — Tevatron-era
fbarn   = exact(1e-15)          * barn          # femtobarn — LHC standard
abarn   = exact(1e-18)          * barn          # attobarn — rare-process searches
ubarn   = μbarn                                  # ASCII alias


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------
W       = J / _s                                # watt — base derived
mW      = exact(1e-3)           * W             # small power (audio, signal)
μW      = exact(1e-6)           * W             # microwatt
kW      = exact(1000)           * W
MW      = exact(1_000_000)      * W
GW      = exact(1_000_000_000)  * W
hp      = exact(745.699_871_582_270_22) * W     # mechanical horsepower
hp_metric = exact(735.498_75)   * W             # metric / German horsepower
hp_electrical = exact(746)      * W             # used in motor labels


# ---------------------------------------------------------------------------
# Astronomy — radio flux density (jansky)
# ---------------------------------------------------------------------------
# The jansky is the standard unit of spectral flux density in radio
# astronomy.  1 Jy = 10⁻²⁶ W·m⁻²·Hz⁻¹.  Named after Karl Jansky who
# discovered cosmic radio emission in 1932.  Bright radio sources are
# in the 1-1000 Jy range; the cosmic microwave background gives μJy-
# range fluxes for individual galaxies.
Jy      = exact(1e-26)          * W / (_m * _m) * _s    # = W/m²/Hz
mJy     = exact(1e-3)           * Jy
μJy     = exact(1e-6)           * Jy
uJy     = μJy                                            # ASCII alias


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------
# The litre is defined as exactly 0.001 m³ (= 1 dm³).  US/UK gallons differ
# by ~20%; the US gallon is more common in engineering, but both are kept
# under explicit names to avoid ambiguity.
liter   = exact(1e-3)           * _m * _m * _m  # = dm³
litre   = liter                                 # spelling alias
mL      = exact(1e-6)           * _m * _m * _m  # = cm³
dL      = exact(1e-4)           * _m * _m * _m
cc      = mL                                    # cubic centimetre
gal_us  = exact(3.785_411_784e-3) * _m * _m * _m # US liquid gallon (exact)
gal_uk  = exact(4.546_09e-3)      * _m * _m * _m # imperial gallon (exact)
qt_us   = gal_us / exact(4)                     # US quart
pt_us   = qt_us / exact(2)                      # US pint
fl_oz_us = pt_us / exact(16)                    # US fluid ounce
qt_uk   = gal_uk / exact(4)                     # imperial quart
pt_uk   = qt_uk / exact(2)                      # imperial pint
fl_oz_uk = pt_uk / exact(20)                    # imperial fluid ounce (note: 20!)
barrel  = exact(42)             * gal_us        # oil barrel = 42 US gal


# ---------------------------------------------------------------------------
# Time — derived names
# ---------------------------------------------------------------------------
# SI base is the second; common multiples are useful for engineering.
minute  = exact(60)             * _s
hour    = exact(3600)           * _s
day     = exact(86_400)         * _s
week    = exact(7)              * day
year_julian = exact(365.25)     * day           # used in astronomy
year_tropical = exact(365.242_19) * day         # for general "year"
yr      = year_julian                            # canonical short form (astronomy)

# ``year`` and ``month`` — convenient names for everyday durations.
#
# ``year``  — the tropical year (365.242 19 days), the right choice for
#   a general-purpose "year" (it tracks the seasons).  ``yr`` stays the
#   Julian year, which is the astronomy convention; the two differ by
#   about 11 minutes, negligible for everyday work but kept distinct so
#   each field gets its expected value.
#
# ``month`` — there is no exact month (calendar months run 28–31 days),
#   so this is the AVERAGE Gregorian month: a Gregorian year is
#   365.2425 days, and ``month`` = that / 12 = 30.436875 days.  It is a
#   conventional value for expressing a duration "in months", not a
#   calendar-accurate count — use real date arithmetic for that.
year    = year_tropical
month   = exact(365.2425 / 12) * day             # average Gregorian month


# ``HMS`` — a display-target sentinel for ``duration ▶ HMS``.
#
# Unlike ``minute``/``hour``/``year``, ``HMS`` is NOT a unit — it does
# not convert to a single scale factor.  It requests a *formatted*
# display, breaking a duration into days/hours/minutes/seconds
# (``1h 01m 01s``).  ``in_units`` recognises it by name and returns an
# ``_HMSDisplay`` wrapper.
#
# This object exists only so that ``t ▶ HMS`` — which the DSL rewrites
# to ``in_units(t, HMS, "HMS")`` — has a real ``HMS`` name to resolve;
# its value is never used (the dispatch keys off the ``"HMS"`` label
# string).  A unique sentinel instance makes accidental misuse as a
# number fail loudly rather than silently.
class _HMSSentinel:
    """Marker for the ``▶ HMS`` duration-display target.  Not a number,
    not a unit — see the note above."""
    __slots__ = ()
    def __repr__(self):
        return "HMS (duration-display target — use as 'duration \u25b8 HMS')"

HMS = _HMSSentinel()
hms = HMS                                        # lowercase alias

# Astronomy / cosmology / geology time scales.  The age of the universe
# is ≈ 13.8 Gyr; stellar lifetimes range from Myr (massive stars) to
# many Gyr; geological epochs are tens to hundreds of Myr; human
# civilization is on the kyr scale.  Always built on Julian years.
kyr     = exact(1_000)          * yr            # kiloyear  — archaeology, holocene
Myr     = exact(1_000_000)      * yr            # megayear  — geology, stellar lifetimes
Gyr     = exact(1_000_000_000)  * yr            # gigayear — cosmology, age of universe


# ---------------------------------------------------------------------------
# Speed / velocity — common names
# ---------------------------------------------------------------------------
mph     = mile / hour                           # miles per hour
kph     = exact(1000) * _m / hour               # km/h
knot    = nautical_mile / hour                  # = 1.852 km/h


# ---------------------------------------------------------------------------
# Temperature — semantics matter
# ---------------------------------------------------------------------------
# Temperature is the one place where the toolkit's unit system is
# fundamentally limited.  ``forallpeople.Physical`` is a pure scaling
# system: a Physical stores a magnitude and a dimension, and conversions
# between units of the same dimension are pure multiplications.  But
# absolute temperature conversions involve an OFFSET — Kelvin to Celsius
# subtracts 273.15, Celsius to Fahrenheit involves both a scale (9/5)
# and an offset (32).  Forallpeople cannot model this; it treats ``°C``
# as just a display alias for K with identical magnitude.
#
# The pragmatic consequence: temperature variables in this toolkit
# behave as **deltas (differences)** by default, matching the Mathcad
# convention where ``Δ°C = ΔK = 5/9 · Δ°F``.  This is exactly what you
# want for thermal expansion (``ΔL = L · α · ΔT``), heat transfer
# (``Q = m · c · ΔT``), and most engineering calculations, where the
# offset would cancel anyway.
#
# For *absolute* conversions — converting "the room is 22°C" to Kelvin
# or Fahrenheit, where the offset matters — use the explicit functions
# below.  They operate on bare numbers, not Physicals, because the
# offset can't live inside the Physical type.  Convention:
#
#     to_kelvin(25)       # 298.15  (assumes Celsius input)
#     to_celsius(298.15)  # 25.0    (Kelvin → Celsius)
#     to_fahrenheit(25)   # 77.0    (Celsius → Fahrenheit)
#     to_celsius_from_F(77)         # 25.0  (Fahrenheit → Celsius)
#     to_kelvin_from_F(77)          # 298.15  (Fahrenheit → Kelvin)
#
# The naming is verbose on purpose — temperature conversion is one of
# the most-confused operations in engineering software, and explicit
# direction-in-name beats a clever overload every time.
def to_fahrenheit(celsius):
    """Convert a numeric Celsius value to Fahrenheit (absolute, offset-affine).

    Use for absolute temperatures: ``to_fahrenheit(25)`` → ``77.0``.
    For temperature *differences* in °C → °F, multiply by 9/5 directly:
    ``ΔT_F = ΔT_C * 9/5`` (no offset).
    """
    return celsius * 9 / 5 + 32

def to_celsius(kelvin):
    """Convert a numeric Kelvin value to Celsius (absolute, offset-affine).

    Use for absolute temperatures: ``to_celsius(298.15)`` → ``25.0``.
    For Fahrenheit→Celsius use ``to_celsius_from_F`` instead.
    """
    return kelvin - 273.15

def to_kelvin(celsius):
    """Convert a numeric Celsius value to Kelvin (absolute, offset-affine).

    Use for absolute temperatures: ``to_kelvin(25)`` → ``298.15``.
    For temperature *differences* in °C → K, no conversion is needed:
    ΔT in °C and ΔT in K are numerically identical.
    """
    return celsius + 273.15

def to_celsius_from_F(fahrenheit):
    """Convert a numeric Fahrenheit value to Celsius (absolute).

    ``to_celsius_from_F(77)`` → ``25.0``.  For temperature *differences*
    in °F → °C, multiply by 5/9 directly.
    """
    return (fahrenheit - 32) * 5 / 9

def to_kelvin_from_F(fahrenheit):
    """Convert a numeric Fahrenheit value to Kelvin (absolute).

    ``to_kelvin_from_F(77)`` → ``298.15``.
    """
    return (fahrenheit - 32) * 5 / 9 + 273.15

def to_fahrenheit_from_K(kelvin):
    """Convert a numeric Kelvin value to Fahrenheit (absolute).

    ``to_fahrenheit_from_K(298.15)`` → ``77.0``.
    """
    return (kelvin - 273.15) * 9 / 5 + 32


# ---------------------------------------------------------------------------
# Absolute temperature constructors — ``from_degC`` / ``from_degF`` /
# ``from_degR``.
#
# These are what the DSL rewrites a ``°C`` / ``°F`` / ``°R`` *literal*
# into.  ``22 °C`` in source becomes ``from_degC(22)`` at transform time,
# and THIS function turns the reading into a real, offset-applied Kelvin
# ``Physical``: ``22 °C`` → ``295.15 K``.
#
# The distinction that makes temperature units tricky:
#
#   * an ABSOLUTE temperature is a point on the scale — the offset is
#     part of its meaning.  ``22 °C`` and ``295.15 K`` are the SAME
#     point.  These constructors produce that.
#   * a DELTA temperature is a difference — no offset, just a step
#     size.  ``ΔC`` / ``ΔF`` / ``deltaC`` / ``deltaF`` (defined below)
#     stay as they were; they are already correct and untouched.
#
# So ``°C`` (absolute) and ``ΔC`` (delta) are now cleanly different
# things, which is exactly the physics.  Arithmetic caveat worth
# knowing: ``°C + °C`` is meaningless (adding two scale *points* —
# ``20 °C + 5 °C`` would give ~588 K); the sensible operations are
# ``°C + ΔC`` (a point plus a difference → a point) and ``°C − °C``
# (two points → a difference).
def from_degC(celsius):
    """Absolute temperature from a Celsius reading → Kelvin ``Physical``.

    ``from_degC(22)`` → ``295.15 K``.  This is what a ``°C`` literal in
    DSL source becomes: ``22 °C`` is rewritten to ``from_degC(22)``.

    The argument is the thermometer reading (it may be a plain number
    or a sigfig ``Sig`` — both work, the ``+`` and ``*`` flow through).
    It may also be a list / matrix / array of readings (from a
    ``[..] °C`` range or list literal), in which case every element is
    converted.  The result is a genuine Kelvin quantity, so it carries
    its unit into every downstream calculation correctly.
    """
    orig = celsius
    celsius = _coerce_temp_sequence(celsius)
    return _tag_temp_scale((celsius + 273.15) * _si_internal.K,
                           orig, "degC")


def from_degF(fahrenheit):
    """Absolute temperature from a Fahrenheit reading → Kelvin ``Physical``.

    ``from_degF(72)`` → ``295.37… K``.  This is what a ``°F`` literal in
    DSL source becomes: ``72 °F`` is rewritten to ``from_degF(72)``.
    Accepts a list / array of readings too (``[..] °F``).
    """
    orig = fahrenheit
    fahrenheit = _coerce_temp_sequence(fahrenheit)
    return _tag_temp_scale(((fahrenheit - 32) * 5 / 9 + 273.15) * _si_internal.K,
                           orig, "degF")


def from_degR(rankine):
    """Absolute temperature from a Rankine reading → Kelvin ``Physical``.

    Rankine is an absolute scale (its zero IS absolute zero), so the
    conversion is a pure scale factor with no offset: ``K = °R × 5/9``.
    ``from_degR(491.67)`` → ``273.15 K``.  This is what a ``°R`` literal
    becomes.  Accepts a list / array of readings too.
    """
    orig = rankine
    rankine = _coerce_temp_sequence(rankine)
    return _tag_temp_scale((rankine * 5 / 9) * _si_internal.K,
                           orig, "degR")


def _tag_temp_scale(kelvin_physical, original, scale):
    """Wrap an absolute-temperature ``Physical`` in a ``Sig`` carrying a
    ``_temp_scale`` hint, so it DISPLAYS in the scale it was written in
    (``100 °C`` shows ``100 °C``, not ``373.15 K``).  The significant-figure
    count is inherited from the original reading when it was a ``Sig``.

    For an ARRAY temperature (a ``[..] °C`` range/list), each element is
    wrapped in its own scale-tagged ``Sig`` inside a ``CommaArray`` — a
    numpy object-array doesn't reliably carry a custom attribute through
    its operations, so the scale has to live on the elements, where the
    array's element-by-element formatter reads it.
    """
    from .sigfig import Sig, _sf_of, sigfigs_of
    try:
        import numpy as _np
        if isinstance(kelvin_physical, _np.ndarray):
            from .circuit_dsl import CommaArray
            # Per-element sf: if the ORIGINAL sequence was a list/array of
            # Sigs (a literal list or a range, each element sf-bearing),
            # read each element's sf; otherwise fall back to the
            # array-level sf, then infinite.
            elem_sfs = None
            try:
                if hasattr(original, "__len__") and not isinstance(original, str):
                    seq = (original.ravel().tolist()
                           if hasattr(original, "ravel") else list(original))
                    elem_sfs = [sigfigs_of(e) for e in seq]
            except Exception:
                elem_sfs = None
            default_sf = _sf_of(original)
            if default_sf is None:
                default_sf = float("inf")
            flat = kelvin_physical.ravel().tolist()
            tagged = []
            for i, el in enumerate(flat):
                sf = (elem_sfs[i] if elem_sfs and i < len(elem_sfs)
                      else default_sf)
                s = Sig(el, sf)
                try:
                    s._temp_scale = scale
                except Exception:
                    pass
                tagged.append(s)
            arr = _np.array(tagged, dtype=object).reshape(kelvin_physical.shape)
            return CommaArray(arr)
    except ImportError:
        pass
    sf = _sf_of(original)
    if sf is None:
        sf = float("inf")
    s = Sig(kelvin_physical, sf)
    try:
        s._temp_scale = scale
    except Exception:
        pass
    return s


def _coerce_temp_sequence(x):
    """If ``x`` is a plain Python list / tuple (e.g. from a ``[..] °C``
    list literal that became ``_as_matrix([...])``), convert it to a numpy
    array so the offset/scale arithmetic applies element-wise.  Scalars,
    ``Sig``, numpy arrays, and ``CommaArray`` pass through unchanged (they
    already support ``+``/``*`` directly)."""
    if isinstance(x, (list, tuple)):
        import numpy as _np
        return _np.array([float(e) for e in x], dtype=float)
    return x


# ---------------------------------------------------------------------------
# Delta-Fahrenheit unit marker — a Physical of dimension K with magnitude
# 5/9, so that ``45 ΔF`` represents the equivalent ΔK.  Useful when a
# datasheet quotes a temperature coefficient in °F⁻¹: writing
# ``α := 6e-6 / ΔF`` then ``ΔL = L · α · ΔT_F`` gives the right answer
# because ΔF carries the 5/9 scale through forallpeople's arithmetic.
#
# Naming: ``ΔF`` and ``ΔC`` are aliases for ΔK (numerically) — pick the
# one that matches your input's notation.  ``ΔC`` and ``ΔK`` both equal
# K with magnitude 1.
# ---------------------------------------------------------------------------
# Δ-temperature unit markers — ``ΔK`` / ``ΔC`` / ``ΔF``
# ---------------------------------------------------------------------------
#
# A ``ΔX`` is used two ways, and they must behave differently:
#
#   * as a LITERAL MULTIPLIER — ``45 ΔC`` (transform: ``_S(45) * deltaC``)
#     → a persistent ``_DeltaTemp`` (a temperature *difference* that
#     displays as ``45 ΔC`` and survives arithmetic).
#   * as a UNIT IN AN EXPRESSION — ``α / ΔF`` (a per-degree temperature
#     coefficient) → must stay a plain ``Physical`` (1 K, or 5/9 K for
#     ΔF), so dividing/multiplying carries the right kelvin scale.
#
# The distinguishing signal is the OPERATOR: ``n * ΔX`` (``__rmul__``)
# makes a ``_DeltaTemp``; ``x / ΔX`` (``__rtruediv__``) keeps the plain
# kelvin-Physical behaviour for coefficients.  ``_DeltaUnit`` is a thin
# marker that carries the kelvin scale + the display unit name and routes
# these two cases.
class _DeltaUnit:
    """Marker for a Δ-temperature unit (``ΔK``/``ΔC``/``ΔF``).

    ``scale`` is the kelvin size of one of these degrees (1 for ΔK/ΔC,
    5/9 for ΔF); ``name`` is the display unit (``"ΔK"`` etc.).
    """

    __slots__ = ("scale", "name")

    def __init__(self, scale, name):
        self.scale = float(scale)
        self.name = name

    def _kelvin_physical(self):
        # The plain ``Physical`` (kelvin) this unit represents, for the
        # coefficient / expression-unit use.
        return _si_internal.K * self.scale

    # ----- literal-multiplier use: ``n * ΔX`` → _DeltaTemp -----
    def __rmul__(self, n):
        # ``n`` is a number or ``Sig``.  Build a temperature difference of
        # ``n`` of these degrees = ``n * scale`` kelvin of span.
        from .sigfig import _DeltaTemp, _unwrap, _sf_of
        nv = _unwrap(n)
        try:
            kelvin = float(nv) * self.scale
        except Exception:
            # Not a plain number (e.g. a Physical) — fall back to the
            # plain-unit product so ``ΔX`` still behaves as a unit.
            return self._kelvin_physical().__rmul__(n)
        return _DeltaTemp(kelvin, self.name, _sf_of(n))

    def __mul__(self, o):
        # ``ΔX * y`` (unit on the left) — treat as the plain kelvin unit
        # times ``y`` (rare; keeps unit algebra working).
        return self._kelvin_physical() * o

    # ----- expression-unit use: ``x / ΔX`` → plain Physical -----
    def __rtruediv__(self, x):
        # ``α / ΔF`` — a per-degree coefficient.  Divide by the plain
        # kelvin unit so the scale (5/9 for ΔF) threads through.
        return x / self._kelvin_physical()

    def __truediv__(self, o):
        return self._kelvin_physical() / o

    # ----- power (``ΔF**2`` etc.) and rendering -----
    def __pow__(self, p):
        return self._kelvin_physical() ** p

    def __repr__(self):
        return self.name

    def __float__(self):
        return self.scale


ΔK = _DeltaUnit(1.0, "ΔK")
ΔC = _DeltaUnit(1.0, "ΔC")
ΔF = _DeltaUnit(5.0 / 9.0, "ΔF")
# Aliases for the ASCII forms and the DSL normalizer (which rewrites
# ``ΔF`` → ``deltaF`` before tokenization).
deltaK = ΔK
deltaC = ΔC
deltaF = ΔF
# ``degF`` / ``degR`` as DIFFERENCE units (the toolkit's delta-by-default
# convention): ``25 degF`` means a 25°F temperature *difference*, not an
# absolute reading.  Same 5/9-kelvin scale as ΔF.
degF = _DeltaUnit(5.0 / 9.0, "ΔF")
degR = _DeltaUnit(5.0 / 9.0, "ΔF")


__all__ = [
    # Length — imperial
    "inch", "ft", "yard", "mile", "nautical_mile", "thou", "mil",
    "parsec", "pc", "ly", "au",
    # Length — extragalactic
    "kpc", "Mpc", "Gpc",
    # Length — metric prefixes
    "km", "hm", "dam", "dm", "fm", "Å",
    # Time — metric prefixes
    "ns", "ps", "fs", "ks",
    # Mass — metric prefixes
    "g", "mg", "μg", "ng",
    # Frequency
    "Hz", "kHz", "MHz", "GHz", "THz",
    # Voltage / current — large prefixes
    "kV", "MV", "mA", "kA",
    # Mass — imperial
    "lb", "lbm", "oz", "grain", "slug", "stone", "ton_us", "ton_uk",
    "tonne", "ozt",
    # Force — full prefix family
    "N", "newton", "μN", "mN", "kN", "MN", "GN", "lbf", "ozf", "kgf",
    "dyne",
    # Charge
    "C", "coulomb", "μC", "mC", "nC", "pC", "kC",
    # Capacitance — pF, nF, mF (μF lives in calc_symbols.py)
    "pF", "nF", "mF",
    # Standard gravity (both spellings)
    "g_0", "g_n",
    # Pressure
    "Pa", "hPa", "kPa", "MPa", "GPa", "bar", "mbar", "atm",
    "torr", "mmHg", "psi", "ksi", "inH2O",
    # Energy
    "J", "kJ", "MJ", "GJ", "cal", "Cal", "kcal", "BTU", "kWh", "Wh",
    "eV", "keV", "MeV", "GeV", "TeV", "PeV", "EeV", "erg",
    # Particle-physics mass units (eV/c²) — see comments above
    "eV_per_c2", "keV_per_c2", "MeV_per_c2", "GeV_per_c2", "TeV_per_c2",
    "u", "Da", "amu",
    # Particle-physics momentum units (eV/c family)
    "eV_per_c", "keV_per_c", "MeV_per_c", "GeV_per_c", "TeV_per_c",
    # Torque — display-preserving (N·m stays N·m, never J)
    "Nm", "mNm", "kNm", "MNm", "Nmm", "lbf_ft", "lbf_inch", "ozf_inch",
    # Particle-physics cross sections (barn family)
    "barn", "mbarn", "μbarn", "ubarn", "nbarn", "pbarn", "fbarn", "abarn",
    # Astronomical masses, radii, luminosity, temperature
    "M_sun", "M_earth", "M_jupiter", "M_moon",
    "R_sun", "R_earth", "R_jupiter",
    "L_sun", "T_sun",
    # Astronomy — radio flux density
    "Jy", "mJy", "μJy", "uJy",
    # Power
    "W", "mW", "μW", "kW", "MW", "GW", "hp", "hp_metric", "hp_electrical",
    # Volume
    "liter", "litre", "mL", "dL", "cc",
    "gal_us", "gal_uk", "qt_us", "pt_us", "fl_oz_us",
    "qt_uk", "pt_uk", "fl_oz_uk", "barrel",
    # Time — derived
    "minute", "hour", "day", "week", "year_julian", "year_tropical", "yr",
    "year", "month", "HMS", "hms",
    # Astronomy / geology / cosmology time scales
    "kyr", "Myr", "Gyr",
    # Speed
    "mph", "kph", "knot",
    # Temperature helpers
    # Temperature helpers — see comment above the functions for the
    # offset/delta distinction.
    "to_fahrenheit", "to_celsius", "to_kelvin",
    "to_celsius_from_F", "to_kelvin_from_F", "to_fahrenheit_from_K",
    "from_degC", "from_degF", "from_degR",
    "ΔK", "ΔC", "ΔF", "deltaK", "deltaC", "deltaF",
    "degF", "degR",
]
