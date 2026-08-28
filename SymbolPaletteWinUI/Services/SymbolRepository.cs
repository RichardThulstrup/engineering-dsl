using SymbolPaletteWinUI.Models;

namespace SymbolPaletteWinUI.Services;

public static class SymbolRepository
{
    // Each ``SymbolEntry`` carries the EXACT text pasted on click as its
    // ``Text``; the optional ``Description`` is the short hover text.  For
    // almost every entry that is all there is — the glyph is its own
    // label, so what you see is what you paste.
    //
    // A few entries need more: a button FACE that differs from the
    // paste-text (the newline glyph ``↵`` does not render in the button
    // font), or a longer ``Detail`` explanation.  The ``Labelled`` factory
    // below builds those; ``E`` stays terse for the common case.
    //
    // Every tab is divided into one or more named sections — each with a
    // short heading shown as a caption above its buttons.  A tab with
    // little content (Greek, Symbolic, Loops) still uses a single named
    // section rather than an unlabelled one, so the heading style is
    // consistent across the whole palette.
    public static IReadOnlyList<SymbolGroup> GetGroups() => _groups;

    // Common case: paste-text doubles as the button label.
    private static SymbolEntry E(string text, string? desc = null) => new(text, desc);

    // Full-control factory: explicit button label and/or long detail text.
    // ``label`` null  → the UI derives the face from ``text``.
    // ``detail`` null → hover shows just the short ``desc``.
    private static SymbolEntry Labelled(
        string text, string? label, string? desc, string? detail = null)
        => new(text, desc, label, detail);

    private static SymbolSection S(string heading, IReadOnlyList<SymbolEntry> entries) => new(heading, entries);

    private static readonly IReadOnlyList<SymbolGroup> _groups =
    [
        // ===============================================================
        // Math glyphs
        // ===============================================================
        new("Math", IconFile: "math.svg", Sections: [
            S("Constants & basic operators", [
                E("π", "pi (sympy.pi); ratio of circumference to diameter"),
                E("∞", "infinity"),
                E("·", "multiplication dot — same as * in DSL"),
                E("×", "multiplication cross — same as * in DSL"),
                E("÷", "division — same as /"),
                E("±", "plus-minus; in DSL produces ±-tolerance objects"),
                E("−", "Unicode minus (U+2212), wider than ASCII -"),
                E("≈", "approximately equal — used for tolerant comparison"),
                E("≠", "not equal"),
                E("≤", "less than or equal"),
                E("≥", "greater than or equal"),
                E("∝", "proportional to"),
            ]),
            S("Powers, roots, exponents", [
                E("↑", "power arrow — math-textbook notation for exponent; 2 ↑ 10 = 1024 (rewrites to **)"),
                E("²", "superscript 2 — postfix square in DSL: x² = x**2"),
                E("³", "superscript 3 — postfix cube: x³ = x**3"),
                E("⁻¹", "inverse — x⁻¹ = 1/x"),
                E("⁻²", "x⁻² = 1/x²"),
                E("√", "square root glyph (use as prefix: √2)"),
                E("³√", "cube root (prefix: ³√27 = 3)"),
            ]),
            S("Common math functions", [
                E("sin()", "sine — radians by default; sympy-aware"),
                E("cos()", "cosine"),
                E("tan()", "tangent"),
                E("asin()", "arcsine — inverse sin"),
                E("acos()", "arccosine"),
                E("atan()", "arctangent"),
                E("atan2(, )", "two-argument atan; atan2(y, x)"),
                E("exp()", "e^x"),
                E("ln()", "natural logarithm — base e"),
                E("log₂()", "logarithm base 2"),
                E("log₁₀()", "logarithm base 10"),
                E("log(, )", "general-base log — log(value, base)"),
                E("Γ()", "gamma function — Γ(n+1) = n!"),
            ]),
            S("Calculus glyphs", [
                E("∂", "partial derivative"),
                E("∇", "nabla / gradient"),
                E("∫", "integral"),
            ]),
            S("Fractions", [
                E("½", "one half"),
                E("⅓", "one third"),
                E("¼", "one quarter"),
                E("¾", "three quarters"),
                E("⅔", "two thirds"),
                E("⅛", "one eighth"),
                E("⅜", "three eighths"),
            ]),
        ]),

        // ===============================================================
        // Greek alphabet
        // ===============================================================
        new("Greek", IconFile: "greek.svg", Sections: [S("Greek letters", [
            E("α", "alpha"), E("β", "beta"), E("γ", "gamma"), E("δ", "delta"),
            E("ε", "epsilon"), E("ζ", "zeta"), E("η", "eta"), E("θ", "theta"),
            E("ι", "iota"), E("κ", "kappa"), E("λ", "lambda"), E("μ", "mu"),
            E("ν", "nu"), E("ξ", "xi"), E("ο", "omicron"), E("π", "pi"),
            E("ρ", "rho"), E("σ", "sigma"), E("τ", "tau"), E("υ", "upsilon"),
            E("φ", "phi"), E("χ", "chi"), E("ψ", "psi"), E("ω", "omega"),
            E("Γ", "uppercase gamma — also gamma function in DSL"),
            E("Δ", "uppercase delta — common as a difference"),
            E("Θ", "uppercase theta"),
            E("Λ", "uppercase lambda"),
            E("Ξ", "uppercase xi"),
            E("Π", "uppercase pi — also product in DSL"),
            E("Σ", "uppercase sigma — also sum in DSL"),
            E("Φ", "uppercase phi"),
            E("Ψ", "uppercase psi"),
            E("Ω", "uppercase omega — also resistance unit (ohm)"),
        ])]),

        // ===============================================================
        // Subscripts and superscripts — split into digits, operators, letters
        //
        // Note on coverage: not every Latin letter has a Unicode subscript
        // or superscript form.  Subscript misses b, c, d, f, g, q, w, y, z;
        // superscript misses q.  We include only what Unicode actually
        // assigns; missing letters can be approximated with a regular
        // letter or by writing the index inline (x_q rather than x_q-with-
        // tiny-q).
        // ===============================================================
        new("Sub/Sup", IconFile: "sub-sup.svg", Sections: [
            S("Subscript digits & operators", [
                E("₀"), E("₁"), E("₂"), E("₃"), E("₄"),
                E("₅"), E("₆"), E("₇"), E("₈"), E("₉"),
                E("₊", "subscript plus"),
                E("₋", "subscript minus"),
                E("₍", "subscript open-paren"),
                E("₎", "subscript close-paren"),
                E("ₐ", "subscript a"),
                E("ₑ", "subscript e"),
                E("ₕ", "subscript h"),
            ]),
            S("Subscript letters (indices)", [
                E("ᵢ", "subscript i — common index"),
                E("ⱼ", "subscript j — common index"),
                E("ₖ", "subscript k — common index"),
                E("ₗ", "subscript l"),
                E("ₘ", "subscript m"),
                E("ₙ", "subscript n — common index"),
                E("ₒ", "subscript o"),
                E("ₚ", "subscript p"),
                E("ᵣ", "subscript r"),
                E("ₛ", "subscript s"),
                E("ₜ", "subscript t"),
                E("ᵤ", "subscript u"),
                E("ᵥ", "subscript v"),
                E("ₓ", "subscript x"),
            ]),
            S("Superscript digits & operators", [
                E("⁰"), E("¹"), E("²"), E("³"), E("⁴"),
                E("⁵"), E("⁶"), E("⁷"), E("⁸"), E("⁹"),
                E("⁺", "superscript plus"),
                E("⁻", "superscript minus"),
                E("⁽", "superscript open-paren"),
                E("⁾", "superscript close-paren"),
            ]),
            S("Superscript letters", [
                E("ᵃ"), E("ᵇ"), E("ᶜ"), E("ᵈ"), E("ᵉ"),
                E("ᶠ"), E("ᵍ"), E("ʰ"),
                E("ⁱ"), E("ʲ"), E("ᵏ"), E("ˡ"), E("ᵐ"),
                E("ⁿ", "superscript n — typical exponent index"),
                E("ᵒ"), E("ᵖ"), E("ʳ"), E("ˢ"),
                E("ᵗ"), E("ᵘ"), E("ᵛ"), E("ʷ"),
                E("ˣ"), E("ʸ"), E("ᶻ"),
            ]),
        ]),

        // ===============================================================
        // Engineering — toolkit-specific glyphs the DSL itself relies on
        // ===============================================================
        new("Engineering", IconFile: "engineering.svg", Sections: [
            S("Operators & assignment", [
                E("‖", "parallel — DSL operator for parallel resistance: R1 ‖ R2"),
                E("∠", "angle / phase — DSL phasor operator: 5 ∠ 30°"),
                E("±", "plus-minus — produces a ±-tolerance Range"),
                E(":=", "math-style assignment — DSL rewrites to = (canonical form)"),
                E("←", "alternative assignment glyph — also rewrites to ="),
                E("→", "lambda / def return-type arrow"),
                E("▸", "display-preference operator — value ▸ target. Inert under arithmetic; sets how a value is shown (unit, integer base, or axis label). Apply on output, at the edge of an expression."),
                E("⌊", "left floor bracket"),
                E("⌋", "right floor bracket"),
                E("⌈", "left ceiling bracket"),
                E("⌉", "right ceiling bracket"),
                E("∑", "summation — n-ary sum operator (= Σ; both forms work in DSL)"),
                E("∏", "product — n-ary product operator (= Π; both forms work in DSL)"),
                E("Γ", "gamma function"),
                E("·", "multiplication dot"),
                E("×", "multiplication cross"),
            ]),
            S("Prefixes & common glyphs", [
                E("Ω", "ohm — electrical resistance unit"),
                E("μ", "micro prefix (= 1e-6)"),
                E("ℏ", "reduced Planck constant (h-bar)"),
                E("ε", "small quantity / permittivity"),
            ]),
            S("Temperature — absolute scales", [
                // °C/°F/°R are real offset units: a literal applies the
                // scale's offset.  ``22 °C`` becomes from_degC(22) =
                // 295.15 K.  These are POINTS on a scale.
                //
                // Only the two-character forms ``°C`` / ``°F`` (degree
                // sign + letter) get buttons — those are what a keyboard
                // produces.  The DSL ALSO accepts the precomposed single
                // glyphs ``℃`` (U+2103) / ``℉`` (U+2109) so that text
                // pasted from outside just works, but those are visually
                // indistinguishable from the two-char forms on a button,
                // so a separate palette entry would only be confusing
                // noise — the engineer cannot tell which is which.
                E("°", "degree — postfix angle operator: 30° → radians. A BARE ° is an angle; °C/°F/°R are temperatures."),
                E("°C", "degree Celsius — absolute. 22 °C is stored as 295.15 K (offset applied)."),
                E("°F", "degree Fahrenheit — absolute. 72 °F is stored as 295.37 K."),
                E("°R", "degree Rankine — absolute. Rankine's zero is absolute zero, so °R = K × 5/9 (no offset)."),
            ]),
            S("Temperature — delta (difference) units", [
                // ΔC/ΔF/ΔK are temperature DIFFERENCES — no offset.
                // Keep these distinct from the absolute °C/°F/°R above.
                E("ΔK", "delta-kelvin — a temperature DIFFERENCE of 1 K (no offset)."),
                E("ΔC", "delta-Celsius — a temperature DIFFERENCE; 1 ΔC = 1 K. Use for changes, e.g. ΔT := 60 ΔC."),
                E("ΔF", "delta-Fahrenheit — a temperature DIFFERENCE; 1 ΔF = 5/9 K."),
                E("ppm/°C", "per-degree coefficient — °C used as a unit (after /) is a delta. e.g. 100 ppm/°C · ΔT."),
            ]),
        ]),

        // ===============================================================
        // Sets — set-theory operators and constructions.  All map to
        // Python set operators or membership keywords via the DSL's
        // ``normalize_source`` / ``rewrite_set_membership_swap`` passes.
        //
        // Two sub-sections: membership (∈ ∉ ∋ ∌ ∅) which mostly behave
        // like comparison operators in expressions, and operations
        // (∩ ∪ ∖ △ etc.) which combine sets into new sets.  Subset
        // ordering (⊆ ⊇ ⊂ ⊃) gets its own section because the
        // strict-vs-non-strict distinction is worth surfacing.
        // ===============================================================
        new("Sets", IconFile: "sets.svg", Sections: [
            S("Membership", [
                E("∅", "empty set — translates to set() (NOT {}, which is an empty dict in Python)"),
                E("∈", "element of — A ∈ B becomes A in B"),
                E("∉", "not element of — A ∉ B becomes A not in B"),
                E("∋", "contains — A ∋ x becomes x in A (operands swapped)"),
                E("∌", "does not contain — A ∌ x becomes x not in A (swapped)"),
            ]),
            S("Operations", [
                E("∩", "intersection — A ∩ B becomes A & B"),
                E("∪", "union — A ∪ B becomes A | B"),
                E("∖", "set difference (U+2216 SET MINUS, not backslash) — A ∖ B becomes A - B"),
                E("△", "symmetric difference — A △ B becomes A ^ B"),
                E("⊕", "alternate symmetric-difference glyph — same as △"),
                E("|{1, 2, 3}|", "cardinality — |S| gives the number of elements. The |…| bars are abs() for a number, len() for a set."),
            ]),
            S("Subset / superset", [
                E("⊆", "subset or equal — A ⊆ B becomes A <= B"),
                E("⊇", "superset or equal — A ⊇ B becomes A >= B"),
                E("⊂", "proper subset (strict) — A ⊂ B becomes A < B"),
                E("⊃", "proper superset (strict) — A ⊃ B becomes A > B"),
            ]),
            S("Constructors", [
                E("set()", "empty set — same as ∅"),
                E("set([])", "set from iterable"),
                E("frozenset()", "immutable set"),
                E("{1, 2, 3}", "set literal — at least one element required"),
                E("{x for x ∈ S}", "set comprehension"),
                E("{x for x ∈ S if }", "set comprehension with filter"),
            ]),
        ]),

        // ===============================================================
        // Constants — grouped by domain.  Names paste verbatim; whether
        // they're defined depends on the user's environment.  ``π``, ``e``,
        // ``i``, ``au``, ``ly``, ``parsec`` ship with the toolkit; others
        // (``c``, ``h``, ``k_B``, ``G`` …) are typical names but the user
        // may need to define them or import them from a constants module.
        // ===============================================================
        new("Constants", IconFile: "constants.svg", Sections: [
            S("Mathematical", [
                E("π", "pi ≈ 3.141 592 653 5 — circumference / diameter"),
                E("e", "Euler's number ≈ 2.718 281 8 — base of natural log"),
                E("i", "imaginary unit, √(−1)"),
                E("∞", "infinity"),
                E("φ", "golden ratio ≈ 1.618 033 9"),
                E("γ", "Euler-Mascheroni constant ≈ 0.577 215 7"),
            ]),
            S("Fundamental physics", [
                E("c", "speed of light in vacuum = 299 792 458 m/s (exact)"),
                E("h", "Planck constant = 6.626 070 15 × 10⁻³⁴ J·s (exact)"),
                E("ℏ", "reduced Planck constant = h/(2π) ≈ 1.055 × 10⁻³⁴ J·s"),
                E("G", "gravitational constant ≈ 6.674 30 × 10⁻¹¹ m³/(kg·s²)"),
                E("k_B", "Boltzmann constant = 1.380 649 × 10⁻²³ J/K (exact)"),
                E("kᵦ", "Boltzmann constant (subscript form — same as k_B)"),
                E("N_A", "Avogadro number = 6.022 140 76 × 10²³ /mol (exact)"),
                E("Nᴬ", "Avogadro (superscript form — same as N_A)"),
                E("R_gas", "gas constant = N_A · k_B ≈ 8.314 J/(mol·K)"),
                E("Rᵍᵃˢ", "gas constant (superscript-gas form — same as R_gas)"),
                E("σ_SB", "Stefan-Boltzmann constant ≈ 5.670 × 10⁻⁸ W/(m²·K⁴)"),
                E("ε_0", "vacuum permittivity ≈ 8.854 × 10⁻¹² F/m"),
                E("εₒ", "vacuum permittivity (subscript-o form — same as ε_0)"),
                E("μ_0", "vacuum permeability ≈ 1.257 × 10⁻⁶ N/A²"),
                E("μₒ", "vacuum permeability (subscript-o form — same as μ_0)"),
                E("q_e", "elementary charge = 1.602 176 634 × 10⁻¹⁹ C (exact)"),
                E("qₑ", "elementary charge (subscript-e form — same as q_e)"),
                E("m_e", "electron mass ≈ 9.109 × 10⁻³¹ kg"),
                E("mₑ", "electron mass (subscript form — same as m_e)"),
                E("m_p", "proton mass ≈ 1.673 × 10⁻²⁷ kg"),
                E("mₚ", "proton mass (subscript form — same as m_p)"),
            ]),
            S("Astronomical / astrophysical", [
                E("au", "astronomical unit ≈ 1.496 × 10¹¹ m — mean Sun-Earth distance"),
                E("ly", "light-year ≈ 9.461 × 10¹⁵ m"),
                E("parsec", "parsec ≈ 3.086 × 10¹⁶ m — distance at which 1 au subtends 1″"),
                E("M_sun", "solar mass ≈ 1.988 × 10³⁰ kg"),
                E("M_earth", "Earth mass ≈ 5.972 × 10²⁴ kg"),
                E("M_jupiter", "Jupiter mass ≈ 1.898 × 10²⁷ kg"),
                E("R_sun", "solar radius ≈ 6.957 × 10⁸ m"),
                E("R_earth", "Earth equatorial radius ≈ 6.371 × 10⁶ m"),
            ]),
            S("Engineering reference values", [
                E("g_0", "standard gravity = 9.806 65 m/s² (exact, by CGPM 1901 definition; defined in extra_units)"),
                E("g_n", "alternate spelling for g_0 (older physics-textbook convention)"),
                E("gₙ", "standard gravity (subscript-n form — same as g_n)"),
                E("T_0", "ice point = 273.15 K (defined)"),
                E("Tₒ", "ice point (subscript-o form — same as T_0)"),
                E("T_zero_C", "0 °C in kelvin = 273.15 K"),
                E("T_room", "20 °C in kelvin = 293.15 K — typical room temperature"),
                E("p_atm", "standard atmosphere = 101 325 Pa"),
            ]),
        ]),

        // ===============================================================
        // Symbolic — sympy operations the toolkit re-exports
        // ===============================================================
        new("Symbolic", IconFile: "symbolic.svg", Sections: [S("Symbolic algebra", [
            E("simplify()", "simplify a symbolic expression"),
            E("expand()", "expand products and powers; opposite of factor"),
            E("factor()", "factor a polynomial expression"),
            E("solve()", "solve an equation or system; e.g. solve(x**2 - 4, x)"),
            E("diff()", "symbolic derivative; diff(expr, variable)"),
            E("integrate()", "symbolic integral; (expr, var) or (expr, (var, a, b))"),
            E("series()", "Taylor / Laurent series; series(expr, var, point, order)"),
            E("limit()", "symbolic limit; limit(expr, var, point)"),
            E("collect()", "collect terms by powers of a variable"),
            E("Eq()", "construct a symbolic equation; Eq(lhs, rhs)"),
            E("subs()", "substitute symbols; expr.subs(x, value)"),
            E("expand_trig()", "expand trig functions of sums into products"),
            E("trigsimp()", "simplify trigonometric expressions"),
            E("Symbol()", "declare a new sympy symbol; x = Symbol('x')"),
            E("Rational()", "exact rational; Rational(1, 3) is 1/3 exactly"),
        ])]),

        // ===============================================================
        // Matrix — a [[…]] literal is a real matrix (sympy-backed), with
        // full linear algebra.  The ᵀ superscript transposes; the ͵
        // separator (U+0375) gives 2-D indexing M₁͵₂.
        // ===============================================================
        new("Matrix", IconFile: "matrix.svg", Sections: [
            S("Construction", [
                E("[[1,2],[3,4]]", "a 2×2 matrix literal — list-of-lists is a real matrix"),
                E("[[1,2,3],[4,5,6]]", "a 2×3 matrix; rows must be equal length"),
                E("[[1],[2],[3]]", "a column vector (3×1)"),
            ]),
            S("Operations", [
                E("ᵀ", "transpose — append to a matrix: Mᵀ"),
                E(".det()", "determinant — M.det()"),
                E(".inv()", "inverse — M.inv()"),
                E(".T", "transpose (function form); same as the ᵀ superscript"),
                E(".eigenvals()", "eigenvalues as {value: multiplicity}"),
                E(".rank()", "rank of the matrix"),
                E(".trace()", "sum of the diagonal"),
            ]),
            S("Indexing (2-D)", [
                // ``͵`` is U+0375 GREEK LOWER NUMERAL SIGN — the 2-D
                // subscript-index separator.  M₁͵₂ → M[1][2].  The button
                // pastes a worked example; the lone separator is also
                // offered so it can be inserted between subscripts.
                E("M₁͵₂", "element at row 1, column 2 (zero-based) — the ͵ separates indices"),
                E("͵", "index separator (U+0375) — place between two subscripts: M₀͵₀"),
            ]),
            S("Display", [
                E("pp()", "typeset a matrix as a bracketed matrix in the notebook"),
                E("▸ hex", "show each cell in hex (display only; operations stay numeric)"),
            ]),
        ]),

        // ===============================================================
        // Plotting — the unit/symbolic-aware plot() helper and fitting
        // ===============================================================
        new("Plotting", IconFile: "plotting.svg", Sections: [
            S("Plot", [
                E("plot(, )", "plot paired data: plot(x, y). Unit-aware — axis labels follow the data."),
                E("plot()", "plot y-only — x defaults to the index 0, 1, 2, …"),
                E("plot(, , (, ))", "plot a symbolic expression swept over a range: plot(expr, var, (xmin, xmax))."),
                E("plot(, , fit=1)", "overlay a linear fit on the first paired-data series (fit=2 quadratic, etc.)."),
                E("plot(, , theme=\"darkgrid\")", "per-plot style — applied locally, does not affect later plots."),
                E(", (\"label\", \"o\")", "series label + matplotlib style string — 'o' markers, '--' dashed, …"),
                E("return_ax=True", "make plot() return the matplotlib Axes for further customisation."),
            ]),
            S("Fitting", [
                E("linefit(, )", "unit-aware linear fit — returns (slope, intercept) as dimensioned quantities."),
                E("polyfit(, , )", "unit-aware polynomial fit — polyfit(x, y, degree)."),
            ]),
            S("Themes", [
                E("list_themes()", "print every available plot theme alias."),
                E("theme=\"darkgrid\"", "dark background with a grid"),
                E("theme=\"whitegrid\"", "light background with a grid"),
                E("theme=\"ggplot\"", "ggplot-style"),
                E("theme=\"538\"", "FiveThirtyEight style"),
                E("theme=\"solarized\"", "Solarized palette"),
                E("theme=\"paper\"", "muted, print-friendly"),
                E("theme=\"presentation\"", "large elements for slides"),
                E("theme=\"colorblind\"", "colourblind-safe palette"),
            ]),
            S("Axis labels via ▸", [
                E("plot(, y ▸ μm)", "display that axis in a chosen unit (μm here)."),
                E("plot(expr, t ▸ ms, (, ))", "label a symbolic sweep axis — t ▸ ms gives a 't [ms]' axis, no conversion."),
                E("plot(x ▸ \"element\", )", "a bare string labels an axis that has no physical unit (a count, an index)."),
            ]),
        ]),

        // ===============================================================
        // ISO 286 — limits and fits for shafts and holes (utils.iso286)
        // ===============================================================
        new("ISO 286", IconFile: "iso286.svg", Sections: [
            S("Tolerance bands", [
                E("hole(, \"H7\")", "hole tolerance band as a Range of mm — hole(nominal, class). e.g. hole(25, \"H7\")."),
                E("shaft(, \"g6\")", "shaft tolerance band as a Range of mm — shaft(nominal, class). e.g. shaft(25, \"g6\")."),
            ]),
            S("Fit analysis", [
                E("fit(, \"H7\", \"g6\")", "analyse a pairing — fit(nominal, hole_class, shaft_class). Classifies clearance / transition / interference."),
                E(".kind", "fit kind — 'clearance', 'transition' or 'interference'."),
                E(".min_clearance", "smallest clearance (negative = interference)."),
                E(".max_clearance", "largest clearance."),
            ]),
            S("Tolerance formulas", [
                E("it_grade(, )", "IT-grade band width — it_grade(diameter, grade). e.g. it_grade(25, 7)."),
                E("tolerance_unit()", "the standard tolerance unit i for a given diameter."),
            ]),
            S("Common classes", [
                // Reference strings — paste into a hole()/shaft() call.
                E("\"H7\"", "hole basis, grade 7 — the usual reference hole."),
                E("\"H8\"", "hole basis, grade 8 — a looser reference hole."),
                E("\"g6\"", "shaft, slight clearance — H7/g6 is a precise running fit."),
                E("\"h6\"", "shaft basis, grade 6 — zero upper deviation."),
                E("\"k6\"", "shaft, transition — H7/k6 may clear or interfere."),
                E("\"p6\"", "shaft, interference — H7/p6 is a press fit."),
                E("\"f7\"", "shaft, free running clearance."),
            ]),
        ]),

        // ===============================================================
        // Units — Mathcad-style ▸ conversion targets, by category
        // ===============================================================
        new("Units", IconFile: "units.svg", Sections: [
            S("Length", [
                E("▸ mm", "convert to millimetres"),
                E("▸ cm", "convert to centimetres"),
                E("▸ m", "convert to metres"),
                E("▸ km", "convert to kilometres"),
                E("▸ inch", "convert to inches (1 inch = 25.4 mm exactly)"),
                E("▸ ft", "convert to feet (12 inches)"),
                E("▸ yard", "convert to yards (3 feet)"),
                E("▸ mile", "convert to miles (5280 feet)"),
            ]),
            S("Mass", [
                E("▸ g", "grams"),
                E("▸ kg", "kilograms"),
                E("▸ tonne", "metric tonnes (1000 kg)"),
                E("▸ oz", "avoirdupois ounces (1 lb / 16)"),
                E("▸ lb", "avoirdupois pounds (= 0.453 592 37 kg, exact)"),
            ]),
            S("Force", [
                E("▸ μN", "micronewtons (strain-gauge scale)"),
                E("▸ mN", "millinewtons"),
                E("▸ N", "newtons — SI base derived (kg·m/s²)"),
                E("▸ kN", "kilonewtons (structural loads)"),
                E("▸ MN", "meganewtons"),
                E("▸ GN", "giganewtons (rocket-thrust scale)"),
                E("▸ lbf", "pounds-force (= lb × g_0)"),
                E("▸ kgf", "kilogram-force (legacy non-SI)"),
            ]),
            S("Pressure", [
                E("▸ Pa", "pascals"),
                E("▸ kPa", "kilopascals"),
                E("▸ MPa", "megapascals"),
                E("▸ bar", "bars (= 100 kPa exactly)"),
                E("▸ atm", "atmospheres (= 101.325 kPa exactly)"),
                E("▸ psi", "pounds per square inch"),
                E("▸ mmHg", "millimetres of mercury (medical / barometric)"),
            ]),
            S("Energy", [
                E("▸ J", "joules"),
                E("▸ kJ", "kilojoules"),
                E("▸ kWh", "kilowatt-hours"),
                E("▸ BTU", "British thermal units"),
                E("▸ cal", "thermochemical calories"),
                E("▸ Cal", "kilocalories (food calorie)"),
                E("▸ eV", "electronvolts"),
            ]),
            S("Power", [
                E("▸ W", "watts"),
                E("▸ kW", "kilowatts"),
                E("▸ MW", "megawatts"),
                E("▸ hp", "mechanical horsepower (= 745.7 W)"),
            ]),
            S("Time", [
                E("▸ ns", "nanoseconds"),
                E("▸ μs", "microseconds"),
                E("▸ ms", "milliseconds"),
                E("▸ s", "seconds"),
                E("▸ minute", "minutes (= 60 s)"),
                E("▸ hour", "hours (= 3600 s)"),
                E("▸ day", "days (= 86 400 s)"),
                E("▸ week", "weeks (= 7 days)"),
                E("▸ month", "months — the average Gregorian month (≈ 30.44 days)."),
                E("▸ year", "years — the tropical year (≈ 365.24 days). 'yr' is the Julian year."),
                E("▸ HMS", "formatted d/h/m/s display — 3661 s ▸ HMS shows '1h 01m 01s'. Sigfig-aware: fields below the precision are dropped."),
            ]),
            S("Frequency", [
                E("▸ Hz", "hertz (= 1/s)"),
                E("▸ kHz", "kilohertz"),
                E("▸ MHz", "megahertz"),
                E("▸ GHz", "gigahertz"),
            ]),
            S("Speed", [
                E("▸ mph", "miles per hour"),
                E("▸ kph", "kilometres per hour"),
                E("▸ knot", "knots (= 1.852 km/h)"),
            ]),
            S("Electrical", [
                E("▸ V", "volts"),
                E("▸ kV", "kilovolts"),
                E("▸ mA", "milliamperes"),
                E("▸ A", "amperes"),
                E("▸ kA", "kiloamperes"),
                E("▸ pC", "picocoulombs (semiconductor charge)"),
                E("▸ nC", "nanocoulombs"),
                E("▸ μC", "microcoulombs"),
                E("▸ mC", "millicoulombs"),
                E("▸ C", "coulombs (= A·s)"),
                E("▸ pF", "picofarads"),
                E("▸ nF", "nanofarads"),
                E("▸ μF", "microfarads"),
                E("▸ mF", "millifarads"),
            ]),
            S("Volume", [
                E("▸ mL", "millilitres"),
                E("▸ liter", "litres"),
                E("▸ gal_us", "US liquid gallons (= 3.785 L)"),
            ]),
            S("Temperature", [
                // Temperature display uses the EXPLICIT deg* names as
                // ▸ targets: ▸ degC / degF / degR / K all work and are
                // offset-correct.  ▸ °C is NOT a scale target — the
                // bare glyph rewrites to the delta unit, ambiguous with
                // ▸ ΔC — so the canonical Celsius display is ▸ degC.
                // Absolute temperatures are ENTERED with the °C/°F/°R
                // literals (see the Engineering tab); stored in kelvin.
                E("▸ degC", "display a temperature on the Celsius scale — offset-correct, e.g. 295.15 K → 22 °C."),
                E("▸ degF", "display a temperature on the Fahrenheit scale."),
                E("▸ degR", "display a temperature on the Rankine scale."),
                E("▸ K", "display a temperature in kelvin (the stored form)."),
                E("22 °C", "ENTER an absolute temperature — °C literal, stored as 295.15 K (see Engineering tab)."),
                E("72 °F", "ENTER an absolute temperature — °F literal, stored in kelvin."),
                E("from_degC()", "absolute temperature from a Celsius value → Kelvin Physical."),
                E("from_degF()", "absolute temperature from a Fahrenheit value → Kelvin Physical."),
                E("to_celsius()", "numeric Kelvin → numeric Celsius (plain number in, plain number out)."),
                E("to_fahrenheit()", "numeric Celsius → numeric Fahrenheit."),
                E("ΔC", "delta-Celsius — a temperature DIFFERENCE (1 ΔC = 1 K), for changes and coefficients."),
                E("ΔF", "delta-Fahrenheit — a temperature DIFFERENCE (1 ΔF = 5/9 K)."),
                E("ΔK", "delta-kelvin — a temperature DIFFERENCE of 1 K."),
            ]),
            S("Integer base (radix display)", [
                // ▸ hex/bin/oct/dec is the same display-preference
                // operator, dispatching on a base name instead of a unit.
                E("▸ hex", "display an integer in hexadecimal — uses subscript notation, e.g. FF₁₆."),
                E("▸ bin", "display an integer in binary — e.g. 1010₂."),
                E("▸ oct", "display an integer in octal — e.g. 17₈."),
                E("▸ dec", "display an integer in plain decimal."),
                E("▸ roman", "display an integer as a Roman numeral — e.g. 2024 → MMXXIV."),
                E("radix(, )", "function form — radix(value, base) for any base 2–36. e.g. radix(255, 16)."),
                E("register_radix(\"\", )", "register a custom integer format — register_radix(name, int→str function)."),
                E("to_roman()", "integer → Roman-numeral string (callable directly)."),
                E("from_roman(\"\")", "Roman-numeral string → integer; the inverse of to_roman. Accepts IIII as well as IV. The DSL spelling is \"…\"ᵣₒₘₑ (see the Various tab)."),
            ]),
        ]),

        // ===============================================================
        // Currency — conversion targets plus management functions.
        // Rates come from Danmarks Nationalbank (refreshed daily).
        // ===============================================================
        new("Currency", IconFile: "currency.svg", Sections: [
            S("Major", [
                E("▸ DKK", "Danish krone — base of the rate table"),
                E("▸ USD", "US dollar"),
                E("▸ EUR", "euro"),
                E("▸ GBP", "British pound"),
                E("▸ JPY", "Japanese yen"),
                E("▸ CHF", "Swiss franc"),
            ]),
            S("Other", [
                E("▸ SEK", "Swedish krona"),
                E("▸ NOK", "Norwegian krone"),
                E("▸ CAD", "Canadian dollar"),
                E("▸ AUD", "Australian dollar"),
                E("▸ CNY", "Chinese yuan (renminbi)"),
                E("▸ HKD", "Hong Kong dollar"),
                E("▸ INR", "Indian rupee"),
                E("▸ PLN", "Polish złoty"),
                E("▸ CZK", "Czech koruna"),
            ]),
            S("Rate management", [
                E("update_currency_rates()", "fetch latest rates from Nationalbanken"),
                E("rates_status()", "show source (live / cache / fallback) and age"),
                E("clear_currency_cache()", "delete on-disk rates cache; forces refresh next call"),
            ]),
        ]),

        // ===============================================================
        // Loops, conditionals, comprehensions
        // ===============================================================
        new("Loops", IconFile: "loops.svg", Sections: [S("Loop templates", [
            E("for j in 1..10:\n    ", "for-loop with inclusive range; cursor lands at body indent"),
            E("for j in [1..10]:\n    ", "alternate inclusive-range syntax"),
            E("for 1 ≤ j ≤ 10:\n    ", "math-style inclusive bounds (DSL)"),
            E("for 1 < j < 10:\n    ", "exclusive bounds"),
            E("for 0 ≤ j < n:\n    ", "half-open range (typical 'for i in range(n)' pattern)"),
            E("for j, x in enumerate():\n    ", "iterate with index; fill in the iterable"),
            E("for x, y in zip(, ):\n    ", "iterate two iterables in parallel"),
            E("while :", "while-loop header — type condition before colon"),
            E("if :", "if statement — type condition before colon"),
            E("elif :", "elif clause — type condition before colon"),
            E("else:\n    ", "else clause; cursor at body indent"),
            E("[ for  in ]", "list comprehension"),
            E("{ for  in }", "set comprehension"),
            E("{:  for  in }", "dict comprehension (key:value)"),
            E("( for  in )", "generator expression"),
            E("range()", "range with stop only"),
            E("range(, )", "range(start, stop)"),
            E("range(, , )", "range(start, stop, step)"),
        ])]),

        // ===============================================================
        // Number-literal forms — DSL-flavored first (the toolkit's
        // subscript-base notation, math-style complex `i`), with the
        // standard Python forms kept as a smaller "Python equivalents"
        // section for cases where the DSL forms can't be used (e.g.
        // pasting into a plain Python script).
        // ===============================================================
        new("Numbers", IconFile: "numbers.svg", Sections: [
            S("Base-suffixed integers", [
                // A based literal must START WITH A DIGIT.  Letter-led
                // tokens for bases 2/8/16 still work (FF₁₆ → 0xFF) since
                // F/A etc. read as hex digits, but a base-36 token led
                // by a letter (zz₃₆) is parsed as a subscript index, not
                // a literal — prefix a 0 (0zz₃₆) or use radix().
                E("1010₂", "binary literal — subscript-2 says \"base 2\"."),
                E("11111111₂", "binary — eight bits."),
                E("17₈", "octal — subscript-8."),
                E("123₈", "octal literal."),
                E("0₁₀", "explicit base-10 — rewrites verbatim."),
                E("1A₁₆", "hex — digit-led, always safe."),
                E("FF₁₆", "hex (letter-led) — works for base 16 because F is a hex digit."),
                E("0FF₁₆", "hex with a leading 0 — the always-safe form for a letter-led literal."),
                E("100₃₆", "base-36, digit-led — letters a–z map to 10–35."),
                E("radix(, )", "function form for any base 2–36 — use this when a literal would be letter-led."),
            ]),
            S("Real & complex", [
                E("3+2i", "complex using sympy's imaginary unit — gives 3 + 2*I (symbolic-friendly)"),
                E("3+2j", "complex using Python's native imaginary unit — gives (3+2j) (numeric)"),
                E("1_000_000", "PEP 515 underscore separator (improves readability for big numbers)"),
                E("π", "pi — exact symbolic constant"),
                E("∞", "infinity — math.inf"),
                E("e", "Euler's number"),
            ]),
            S("Exact & rational", [
                E("_R('0.1')", "exact rational from decimal string — sympy Rational, e.g. 1/10"),
                E("_R('1/7')", "exact rational from fraction string — sympy Rational(1, 7)"),
                E("Rational(, )", "sympy rational; Rational(1, 7) = 1/7"),
                E("Fraction(, )", "stdlib rational (fractions.Fraction)"),
                E("Decimal('')", "stdlib decimal for exact decimal arithmetic"),
                E("exact()", "wrap value as exact (sf=∞) — used for unit constants"),
                E("measured(, )", "wrap value with explicit sigfig count, e.g. measured(120, 3)"),
            ]),
            S("Mode switches", [
                E("set_decimal_literals(True)", "make decimal literals like 0.1 evaluate as Rational"),
                E("set_decimal_literals(False)", "restore ordinary float literals (default)"),
            ]),
        ]),

        // ===============================================================
        // Mixed-bag utilities — pp, pn, common stdlib helpers, datetime
        // ===============================================================
        new("Various", IconFile: "various.svg", Sections: [
            S("Pretty print (toolkit)", [
                E("pp()", "symbolic pretty-print; sympy values render with ⋅ and Greek"),
                E("pn()", "numeric pretty-print; sympy values evaluate to numbers"),
            ]),
            S("Built-ins", [
                E("print()", "stdlib print — note: shows an empty set as set(), not ∅ (it calls str()). Use pp() or print(fmt(x)) for the ∅ glyph."),
                E("type()", "type of an object"),
                E("len()", "length of a sequence / collection"),
                E("abs()", "absolute value"),
                E("round()", "round to nearest integer (banker's rounding) or to N digits"),
                E("sum()", "sum of an iterable; sum([1,2,3]) = 6"),
                E("min()", "minimum"),
                E("max()", "maximum"),
                E("isinstance(, )", "type check"),
                E("hasattr(, '')", "check for attribute by name"),
                E("getattr(, '')", "get attribute by name"),
                E("dir()", "list attributes / methods of an object"),
                E("help()", "show help (docstring + signature)"),
            ]),
            S("Toolkit helpers", [
                E("in_units(, )", "express value in target units; in_units(v, mm/s)"),
                E("sigfigs_of()", "return sigfig count of a Sig"),
                E("exact()", "make value exact (sf=∞)"),
                E("measured(, )", "tag value with explicit sigfig count"),
                E("fmt()", "string form of a value, showing an empty set as ∅ (for the print() path)."),
                E("radix(, )", "display an integer in any base 2–36."),
                E("register_radix(\"\", )", "register a custom integer-display format."),
                E("from_degC()", "absolute temperature from a Celsius reading → Kelvin Physical."),
                E("from_degF()", "absolute temperature from a Fahrenheit reading → Kelvin Physical."),
            ]),
            S("Date / time (input formats)", [
                E("\"today\"ₜᵢₘₑ", "today's date — rewrites to iso(\"today\") → date.today()"),
                E("\"now\"ₜᵢₘₑ", "current local datetime — iso(\"now\")"),
                E("\"utcnow\"ₜᵢₘₑ", "current UTC datetime — iso(\"utcnow\")"),
                E("\"2026-05-10\"ₜᵢₘₑ", "ISO date — fill in the date you want"),
                E("\"2026-05-10T14:30:00\"ₜᵢₘₑ", "ISO datetime — date + T + time"),
                E("\"2026-05-10T14:30:00Z\"ₜᵢₘₑ", "ISO datetime in UTC — Z suffix"),
                E("\"14:30:00\"ₜᵢₘₑ", "ISO time-of-day — date will be today by default"),
                E("\"PT1H30M\"ₜᵢₘₑ", "ISO 8601 duration — 1 hour 30 minutes"),
                E("\"P1Y6M\"ₜᵢₘₑ", "ISO 8601 duration — 1 year 6 months"),
                E("\"PT15M30S\"ₜᵢₘₑ", "ISO 8601 duration — 15 minutes 30 seconds"),
            ]),
            S("Date / time (functions)", [
                E("datetime.now()", "current local datetime — stdlib form"),
                E("datetime.today()", "today's date as datetime"),
                E("datetime(, , )", "specific date: datetime(year, month, day)"),
                E("timedelta(days=)", "time difference; days/seconds/hours kwargs"),
                E("time.time()", "Unix timestamp (seconds since 1970)"),
            ]),
            S("Roman numerals (input)", [
                // The ``"…"ᵣₒₘₑ`` literal is the INPUT counterpart of the
                // ``▸ roman`` display tag on the Numbers tab: ``▸ roman``
                // renders an integer AS Roman numerals, ``"…"ᵣₒₘₑ`` reads
                // a Roman string back into a plain int.  Same string-
                // literal-plus-subscript shape as the ``"…"ₜᵢₘₑ`` date
                // templates above.  Both subtractive (IV) and additive
                // (IIII) forms parse — including the IIII clock-face form.
                E("\"MMXXVI\"ᵣₒₘₑ", "Roman-numeral literal → int; \"MMXXVI\" reads as 2026."),
                E("\"MCMXCIV\"ᵣₒₘₑ", "subtractive form — MCMXCIV → 1994."),
                E("\"IIII\"ᵣₒₘₑ", "additive form — IIII → 4 (the traditional clock-face spelling)."),
                E("\"\"ᵣₒₘₑ", "blank Roman literal — fill in the numeral between the quotes."),
                E("from_roman(\"\")", "function form — Roman-numeral string → int, callable directly."),
            ]),
            S("Whitespace characters", [
                // These paste the REAL Unicode glyphs ``⇥`` (U+21E5) and
                // ``↵`` (U+21B5).  The DSL's ⇥/↵ rewriter recognises
                // both and turns them into a tab / newline — so pasting
                // the glyph is exactly right; no escape-sequence text is
                // involved.
                //
                // ``⇥`` renders fine on a button, so it is a plain entry
                // — face and paste-text are the same glyph.
                //
                // ``↵`` does NOT render in the button font (it shows as
                // a missing-glyph box), so its entry uses the Labelled
                // factory: paste-text is the real ``↵`` glyph, but the
                // button FACE shows the ASCII tag ``NL`` instead, which
                // renders everywhere.  If a proper newline icon is added
                // to the project later, swap the label for that.
                Labelled("\u21e5", null,
                    "tab — pastes ⇥ (U+21E5); the DSL turns it into a tab",
                    "Inserts the tab glyph ⇥. The DSL's ⇥/↵ rewriter "
                    + "converts it to a tab — as a \\t escape inside a "
                    + "string literal, or as a separate \\t argument in a "
                    + "function call."),
                Labelled("\u21b5", "NL",
                    "newline — pastes ↵ (U+21B5); the DSL turns it into a newline",
                    "Inserts the newline glyph ↵. The DSL's ⇥/↵ rewriter "
                    + "converts it to a newline. The button shows 'NL' "
                    + "because the ↵ glyph does not render in this font."),
            ]),
        ]),
    ];
}
