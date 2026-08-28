namespace SymbolPaletteWinUI.Models;

/// <summary>
/// One clickable item in the palette.
///
/// A button carries up to four distinct pieces of information, kept as
/// separate fields so each can be chosen independently:
///
///   • <see cref="Text"/>        — the EXACT text pasted on click. This is
///                                 the payload and nothing else; it is
///                                 never altered for display.
///   • <see cref="Label"/>       — what the button FACE shows. Optional.
///                                 When null the UI derives a label from
///                                 <see cref="Text"/> (see <see cref="DisplayLabel"/>).
///                                 Set it when the paste-text cannot be
///                                 shown directly — e.g. the newline glyph
///                                 ``↵`` (U+21B5) does not render in the
///                                 button font, so that entry pastes ``↵``
///                                 but shows a different label.
///   • <see cref="Description"/> — short one-line hover text for the
///                                 status bar. Optional; null means the
///                                 symbol is its own explanation.
///   • <see cref="Detail"/>      — a longer explanation, for a tooltip or
///                                 an expanded help panel. Optional.
///
/// For the overwhelming majority of entries only <see cref="Text"/> (and
/// often <see cref="Description"/>) is set: the glyph is its own label,
/// so what you see is what you paste. The extra fields exist for the few
/// entries where face, payload, and explanation genuinely differ.
/// </summary>
public sealed record SymbolEntry(
    string Text,
    string? Description = null,
    string? Label = null,
    string? Detail = null)
{
    /// <summary>
    /// What the button face should display.
    ///
    /// • If an explicit <see cref="Label"/> was given, that wins.
    /// • Otherwise the paste-<see cref="Text"/> is used, with newlines
    ///   collapsed to a small ``↵`` glyph and continuation-line indent
    ///   trimmed, so a multi-line snippet such as ``for j in 1..10:\n    ``
    ///   shows as ``for j in 1..10: ↵`` rather than a broken two-line
    ///   label. Single-line text is returned unchanged.
    ///
    /// The UI binds to this and never has to decide between Label and
    /// Text itself.
    /// </summary>
    public string DisplayLabel
    {
        get
        {
            if (Label is not null)
                return Label;

            string normalised = Text.Replace("\r\n", "\n").Replace("\r", "\n");
            if (!normalised.Contains('\n'))
                return normalised;

            string[] parts = normalised.Split('\n');
            for (int i = 1; i < parts.Length; i++)
                parts[i] = parts[i].TrimStart();

            return string.Join(" \u21b5 ", parts).TrimEnd(' ', '\u21b5');
        }
    }
}

/// <summary>
/// A named subdivision within a tab.  When ``Heading`` is empty (or
/// whitespace), the section renders as a flat band of buttons with no
/// header — used by tabs that don't subdivide (Math, Greek, etc.).  Tabs
/// that DO subdivide (Units, Constants, Sub/Sup) supply non-empty
/// headings; the UI then renders a small caption above each section.
/// </summary>
public sealed record SymbolSection(
    string Heading,
    IReadOnlyList<SymbolEntry> Symbols);

/// <summary>
/// One tab in the palette.  Always carries a list of sections; tabs without
/// internal subgrouping use a single section with an empty heading.
///
/// <see cref="IconFile"/> is the file name of the tab's selector icon —
/// e.g. ``"math.svg"``.  The file is expected in the project's
/// ``Assets/Icons/`` folder (copied next to the app at build time, see
/// the .csproj).  SVG is preferred — it renders crisply at any DPI and
/// any button size — but a PNG of the same base name works too.
///
/// To customise a tab's icon: drop a new file into ``Assets/Icons/`` and
/// point <see cref="IconFile"/> at it.  No other code changes.
///
/// Optional: when null, or when the named file is missing at runtime,
/// the selector button falls back to showing the tab <see cref="Name"/>
/// as text — so the palette is always usable, even before every icon
/// file has been supplied.
/// </summary>
public sealed record SymbolGroup(
    string Name,
    IReadOnlyList<SymbolSection> Sections,
    string? IconFile = null);
