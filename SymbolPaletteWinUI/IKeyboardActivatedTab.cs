namespace SymbolPaletteWinUI.Controls;

/// <summary>
/// Implemented by tab-content controls that host a real text or numeric
/// input and therefore need the palette window to be activatable while
/// they are shown.
///
/// The palette is normally a non-activating window, so a glyph click
/// never steals foreground from the notebook. Controls that need
/// physical keyboard input (the formula editor, the table builder) are
/// the exception. MainWindow.ApplyActivationForTab checks for this
/// interface to decide whether to suppress activation, and calls
/// <see cref="FocusInput"/> so the user can type the moment the tab
/// opens.
/// </summary>
internal interface IKeyboardActivatedTab
{
    /// <summary>Place keyboard focus on this control's primary input.</summary>
    void FocusInput();
}
