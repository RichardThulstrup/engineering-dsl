using Microsoft.UI;
using Microsoft.UI.Text;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using SymbolPaletteWinUI.Models;
using SymbolPaletteWinUI.Services;
using Windows.Graphics;
using WinRT.Interop;

namespace SymbolPaletteWinUI;

public sealed partial class MainWindow : Window
{
    private const int DefaultWindowWidth = 720;
    private const int DefaultWindowHeight = 560;
    private const int MinimumWindowWidth = 460;
    private const int MinimumWindowHeight = 380;
    private const int MaximumWindowWidth = 1800;
    private const int MaximumWindowHeight = 1400;

    private readonly WindowFocusService _focusService = new();
    private readonly ClipboardPasteService _clipboardPasteService = new();
    private readonly NoActivateWindowService _noActivateWindowService = new();
    private readonly PaletteSettingsService _settingsService = new();
    private readonly PaletteSettings _settings;

    private AppWindow? _appWindow;

    public MainWindow()
    {
        _settings = _settingsService.Load();

        InitializeComponent();

        Closed += MainWindow_Closed;

        ConfigureWindow();
        BuildSymbolTabs();

        // Re-colour the tab icons if the system theme changes while the
        // palette is open.  The icons are theme-specific files (light/
        // vs dark/ subfolder) because an SvgImageSource cannot pick up
        // the theme on its own — ApplyIconTheme re-points them.
        if (Content is FrameworkElement root)
            root.ActualThemeChanged += (_, _) => ApplyIconTheme();

        var hwnd = WindowNative.GetWindowHandle(this);
        _focusService.Start(hwnd, DispatcherQueue);

        // Hand this window's handle to the single-instance service so a
        // later launch can find and surface it instead of opening a
        // second palette.  ``App.SingleInstance`` is the same service
        // instance that already took the process lock in OnLaunched.
        if (Application.Current is App app)
            app.SingleInstance.PublishWindow(hwnd);

        // Apply after the first message-loop turn. App.OnLaunched calls Activate()
        // after this constructor returns; applying here prevents later mouse clicks
        // from stealing activation from Notepad/Jupyter/etc.
        DispatcherQueue.TryEnqueue(() =>
        {
            _noActivateWindowService.Apply(hwnd);
            NativeMethods.SetAlwaysOnTop(hwnd, true);
            ApplyActivationForTab(_activeTabIndex);
        });
    }

    private void ConfigureWindow()
    {
        var hwnd = WindowNative.GetWindowHandle(this);
        var windowId = Win32Interop.GetWindowIdFromWindow(hwnd);
        _appWindow = AppWindow.GetFromWindowId(windowId);

        _appWindow.Title = "Symbol Palette";

        RectInt32 startupRect = GetStartupWindowRect();
        if (_settings.HasWindowPlacement)
            _appWindow.MoveAndResize(startupRect);
        else
            _appWindow.Resize(new SizeInt32(startupRect.Width, startupRect.Height));

        if (_appWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsResizable = true;
            presenter.IsMaximizable = false;
        }
    }

    /// <summary>
    /// Align the window's activation policy with the selected tab. The
    /// symbol tabs need the palette non-activating so a glyph click never
    /// pulls foreground from the notebook; the formula tab needs the
    /// opposite, because its math-field only receives physical keystrokes
    /// when the palette is the activated window.
    /// </summary>
    private void ApplyActivationForTab(int index)
    {
        if (index < 0 || index >= _tabContents.Count)
            return;

        bool needsKeyboard = _tabContents[index] is Controls.IKeyboardActivatedTab;

        _noActivateWindowService.SetSuppressActivation(suppress: !needsKeyboard);

        if (needsKeyboard)
        {
            var hwnd = WindowNative.GetWindowHandle(this);
            NativeMethods.BringWindowToFront(hwnd);

            if (_tabContents[index] is Controls.IKeyboardActivatedTab kbTab)
                kbTab.FocusInput();
        }
        else
        {
            IntPtr target = _focusService.LastExternalWindow;
            if (target != IntPtr.Zero)
                NativeMethods.BringWindowToFront(target);
        }
    }

    private RectInt32 GetStartupWindowRect()
    {
        int width = Clamp(_settings.WindowWidth, MinimumWindowWidth, MaximumWindowWidth);
        int height = Clamp(_settings.WindowHeight, MinimumWindowHeight, MaximumWindowHeight);

        if (!_settings.HasWindowPlacement)
            return new RectInt32(0, 0, DefaultWindowWidth, DefaultWindowHeight);

        var requested = new RectInt32(_settings.WindowX, _settings.WindowY, width, height);
        var virtualScreen = NativeMethods.GetVirtualScreenBounds();

        // If monitor layout changed and the saved position is now off-screen,
        // keep the remembered size but let Windows choose a visible start position.
        if (!Intersects(requested, virtualScreen))
            return new RectInt32(0, 0, width, height);

        return requested;
    }

    // Tab-selector state.  ``_tabButtons`` and ``_tabContents`` are
    // parallel lists, indexed the same as SymbolRepository.GetGroups():
    // _tabButtons[i] is the toggle button in the wrap selector,
    // _tabContents[i] is the (already-built) content panel for that
    // group.  Switching tabs is just swapping TabContentHost.Content.
    private readonly List<ToggleButton> _tabButtons = new();
    private readonly List<UIElement> _tabContents = new();
    private int _activeTabIndex = -1;

    private void BuildSymbolTabs()
    {
        var groups = SymbolRepository.GetGroups();

        for (int i = 0; i < groups.Count; i++)
        {
            SymbolGroup group = groups[i];

            // Content panel for this group: a vertical stack of sections,
            // each an optional heading + a wrap of buttons.  Built once,
            // up front, and kept — switching tabs reuses these panels
            // rather than rebuilding, so tab changes are instant.
            var stack = new StackPanel { Spacing = 10 };
            foreach (var section in group.Sections)
                BuildSectionInto(section, stack);
            _tabContents.Add(stack);

            // Selector button for this group: ICON ONLY.  Icons keep the
            // selector compact so all 14 fit in few rows; the group name
            // is not drawn on the button but appears in the status bar
            // on hover (see TabButton_PointerEntered) and as a tooltip —
            // so an icon that is not self-evident is still identifiable
            // without a click.  A ToggleButton so the active tab reads
            // as pressed; the WrapPanel host lays the buttons across as
            // many rows as the width needs.
            var tabButton = new ToggleButton
            {
                Content = CreateTabFace(group),
                Tag = i,
                MinWidth = 0,
                Padding = new Thickness(8, 6, 8, 6),
            };
            tabButton.Click += TabButton_Click;
            tabButton.PointerEntered += TabButton_PointerEntered;
            tabButton.PointerExited += TabButton_PointerExited;
            // ToolTip as a second affordance — a hover that lingers over
            // the button shows the name as a floating tip, in addition
            // to the status-bar text.
            ToolTipService.SetToolTip(tabButton, group.Name);
            _tabButtons.Add(tabButton);
            TabSelector.Children.Add(tabButton);
        }
        AppendFormulaTab();
        AppendMarkdownTableTab();
        RestoreActiveTab();
    }

    /// <summary>
    /// Pointer-driven activation: keep the palette activatable only while
    /// the pointer is over the formula editor's WebView2 area, so the
    /// notebook regains foreground as the user moves toward the Insert
    /// button. Independent of, and additive to, the tab-level
    /// ApplyActivationForTab — tab switching does the initial activation,
    /// pointer crossings handle the moment-to-moment behavior.
    /// </summary>
    private void SetPaletteActive(bool active)
    {
        _noActivateWindowService.SetSuppressActivation(suppress: !active);

        if (active)
        {
            var hwnd = WindowNative.GetWindowHandle(this);
            NativeMethods.BringWindowToFront(hwnd);
        }
        else
        {
            IntPtr target = _focusService.LastExternalWindow;
            if (target != IntPtr.Zero)
                NativeMethods.BringWindowToFront(target);
        }
    }

    private async void OnFormulaInsertRequested(
        object? sender, Controls.FormulaInsertEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(e.Latex))
            return;

        IntPtr target = _focusService.LastExternalWindow;
        if (target == IntPtr.Zero)
            return;

        // Defensive: ensure foreground is on the target regardless of where
        // the pointer is right now. The pointer-tracking usually does this
        // already, but a click straight from the ∑x tab button to the
        // Insert button never crosses the editor and would skip it.
        _noActivateWindowService.SetSuppressActivation(suppress: true);
        NativeMethods.BringWindowToFront(target);
        await Task.Delay(30);

        if (NativeMethods.IsWordWindow(target))
        {
            await InsertFormulaIntoWord(target, e.Latex);
        }
        else
        {
            // Jupyter / generic markdown: wrap with $...$ or $$...$$.
            string wrapped = e.IsDisplay
                ? "$$\n" + e.Latex + "\n$$"
                : "$" + e.Latex + "$";

            try
            {
                await _clipboardPasteService.AutoPasteTextAsync(
                    wrapped, target, restoreTextClipboard: true);
            }
            catch (Exception ex)
            {
                ClipboardPasteService.CopyText(wrapped);
                StatusText.Text = "Copy only: " + ex.Message;
            }
        }
    }

    /// <summary>
    /// Word path: Alt+= opens an inline equation field at the cursor; the
    /// LaTeX is then pasted into that field and Word converts it to a
    /// typeset equation. Requires Word's equation editor in LaTeX mode
    /// (Equation Tools → Convert → LaTeX). The small delay between
    /// keystroke and paste lets the equation field spawn and take focus.
    /// </summary>
    private async Task InsertFormulaIntoWord(IntPtr wordHwnd, string latex)
    {
        NativeMethods.SendAltEquals();
        await Task.Delay(150);

        try
        {
            await _clipboardPasteService.AutoPasteTextAsync(
                latex, wordHwnd, restoreTextClipboard: true);
        }
        catch (Exception ex)
        {
            ClipboardPasteService.CopyText(latex);
            StatusText.Text = "Copy only: " + ex.Message;
        }
    }
    /// <summary>
    /// Append the WYSIWYG formula editor as one extra tab, after the
    /// repository-driven symbol groups. Its content is a live control
    /// (MathLive in a WebView2), not a generated panel of buttons, so it is
    /// added straight to the parallel tab lists rather than through
    /// SymbolRepository. Index = groups.Count, kept last so the existing
    /// tab indices stay aligned with GetGroups().
    /// </summary>
    private void AppendFormulaTab()
{
    var editor = new Controls.FormulaEditor();

    // TabContentHost is a ScrollViewer, which measures its content with
    // unbounded height — that would collapse the editor's star-sized
    // WebView2 row to its MinHeight. Pinning the editor's height to the
    // viewport makes it fill the host exactly and track window resizes.
    editor.SetBinding(FrameworkElement.HeightProperty,
        new Microsoft.UI.Xaml.Data.Binding
        {
            Source = TabContentHost,
            Path = new PropertyPath("ViewportHeight"),
        });

        editor.InsertRequested += OnFormulaInsertRequested;
        editor.FieldEntered += (_, _) => SetPaletteActive(true);
        editor.FieldExited += (_, _) => SetPaletteActive(false);
        _tabContents.Add(editor);

    // Selector button, same shape as the symbol tabs'. The face reuses
    // the existing text-face helper with a math glyph.
    var tabButton = new ToggleButton
    {
        Content = MakeTabTextFace("\u2211x"),   // "∑x"
        Tag = _tabButtons.Count,                // first index after the groups
        MinWidth = 0,
        Padding = new Thickness(8, 6, 8, 6),
    };
    tabButton.Click += TabButton_Click;
    tabButton.PointerEntered += TabButton_PointerEntered;
    tabButton.PointerExited += TabButton_PointerExited;
    ToolTipService.SetToolTip(tabButton, "Formula editor");

    _tabButtons.Add(tabButton);
    TabSelector.Children.Add(tabButton);
}
    /// <summary>
    /// Build the visual shown on a tab's selector button.
    ///
    /// When the group names an icon file, this returns an
    /// <see cref="Image"/> sourced from a THEME-SPECIFIC copy of the
    /// icon: ``Assets/Icons/light/&lt;file&gt;`` in the light theme,
    /// ``Assets/Icons/dark/&lt;file&gt;`` in the dark theme.  Two sets
    /// are needed because an <see cref="SvgImageSource"/> does NOT
    /// inherit the WinUI control foreground — an SVG ``currentColor``
    /// resolves to the file's own (black) default — so the icon colour
    /// has to be baked into the file.  The ``dark`` set has a near-white
    /// stroke, the ``light`` set a near-black one.
    ///
    /// The chosen <see cref="SymbolGroup"/> is stored on the Image's Tag
    /// so <see cref="ApplyIconTheme"/> can re-point every icon when the
    /// system theme changes at runtime.
    ///
    /// Robustness: image loading in WinUI is asynchronous and a missing
    /// or malformed file does NOT throw here — it raises a failure event
    /// later.  The load-failure events swap the button content to a text
    /// label (the group name) if the file cannot be shown.  Combined
    /// with the no-icon-named case, a tab button is never blank.
    /// </summary>
    private UIElement CreateTabFace(SymbolGroup group)
    {
        // No icon named → text label, immediately.
        if (string.IsNullOrEmpty(group.IconFile))
            return MakeTabTextFace(group.Name);

        var image = new Image
        {
            Width = 20,
            Height = 20,
            // The icon art is square; Uniform keeps aspect ratio.
            Stretch = Microsoft.UI.Xaml.Media.Stretch.Uniform,
            // Hold the group so a later theme change can re-source this
            // Image without rebuilding the whole tab strip.
            Tag = group,
        };

        // A Grid lets the text fallback replace the Image in place
        // without the caller needing to know which one is showing.
        var host = new Grid();
        host.Children.Add(image);

        // ImageFailed catches a missing/!unreadable file (raster decode
        // failure, and a net for SVG too); OpenFailed on the SVG source
        // is wired inside SetIconSource.
        image.ImageFailed += (_, _) => SwapToText(host, group.Name);

        SetIconSource(image, group);
        return host;
    }

    /// <summary>
    /// Point an icon <see cref="Image"/> at the correct themed file for
    /// the current theme.  Called when the icon is first built and again
    /// whenever the theme changes.
    /// </summary>
    private void SetIconSource(Image image, SymbolGroup group)
    {
        if (string.IsNullOrEmpty(group.IconFile))
            return;

        // "dark" or "light" subfolder, by the window's effective theme.
        string themeFolder =
            (Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark
                ? "dark"
                : "light";

        var uri = new Uri(
            $"ms-appx:///Assets/Icons/{themeFolder}/{group.IconFile}");

        bool isSvg = group.IconFile.EndsWith(
            ".svg", StringComparison.OrdinalIgnoreCase);

        if (isSvg)
        {
            var svg = new Microsoft.UI.Xaml.Media.Imaging.SvgImageSource(uri);
            // OpenFailed fires if the SVG is missing or unparseable; fall
            // back to the group name as text on the parent Grid.
            svg.OpenFailed += (_, _) =>
            {
                if (image.Parent is Grid g)
                    SwapToText(g, group.Name);
            };
            image.Source = svg;
        }
        else
        {
            image.Source =
                new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(uri);
        }
    }

    /// <summary>
    /// Re-source every tab icon for the current theme.  Wired to the
    /// root element's ActualThemeChanged so a light/dark switch while
    /// the palette is open updates the icons immediately.
    /// </summary>
    private void ApplyIconTheme()
    {
        foreach (var button in _tabButtons)
        {
            if (button.Content is Grid host)
            {
                foreach (var child in host.Children)
                {
                    if (child is Image img && img.Tag is SymbolGroup grp)
                        SetIconSource(img, grp);
                }
            }
        }
    }

    /// <summary>Replace whatever a tab face is showing with a text label
    /// of the group name — the fallback when an icon cannot load.</summary>
    private static void SwapToText(Grid host, string name)
    {
        host.Children.Clear();
        host.Children.Add(MakeTabTextFace(name));
    }

    private static TextBlock MakeTabTextFace(string name) => new()
    {
        Text = name,
        FontSize = 13,
        VerticalAlignment = VerticalAlignment.Center,
    };

    private void TabButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is ToggleButton btn && btn.Tag is int index)
            SelectTab(index);
    }

    private void TabButton_PointerEntered(object sender, PointerRoutedEventArgs e)
    {
        // The tab buttons are icon-only, so the group name is shown in
        // the status bar on hover — this is how the engineer identifies
        // a tab whose icon is not self-evident without having to click
        // it.  The name is looked up from the repository by the index
        // stored in Tag.
        if (sender is ToggleButton btn && btn.Tag is int index)
        {
            var groups = SymbolRepository.GetGroups();
            if (index >= 0 && index < groups.Count)
                StatusText.Text = groups[index].Name;
        }
    }

    private void TabButton_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        // Clear back to empty when the pointer leaves the tab button —
        // same behaviour as the symbol-button hover.
        StatusText.Text = string.Empty;
    }

    /// <summary>
    /// Make tab <paramref name="index"/> the active one: show its
    /// content, press its selector button, un-press the others.  A
    /// ToggleButton would otherwise let the user un-toggle the active
    /// tab and leave nothing selected; re-pressing the active tab here
    /// simply keeps it selected.
    /// </summary>
    private void SelectTab(int index)
    {
        if (index < 0 || index >= _tabContents.Count)
            return;

        for (int i = 0; i < _tabButtons.Count; i++)
            _tabButtons[i].IsChecked = (i == index);

        TabContentHost.Content = _tabContents[index];
        _activeTabIndex = index;
        ApplyActivationForTab(index);
    }

    private void BuildSectionInto(SymbolSection section, StackPanel parent)
    {
        // Sections with empty/whitespace heading render flat (no caption).
        // This is how the un-subdivided tabs (Math, Greek, Symbolic, …)
        // share the same builder code as the subdivided ones.
        if (!string.IsNullOrWhiteSpace(section.Heading))
        {
            parent.Children.Add(new TextBlock
            {
                Text = section.Heading,
                FontSize = 12,
                FontWeight = FontWeights.SemiBold,
                Opacity = 0.6,
                Margin = new Thickness(2, 6, 2, 0),
            });
        }

        var wrap = new Controls.WrapPanel
        {
            HorizontalSpacing = 6,
            VerticalSpacing = 6,
        };

        foreach (var entry in section.Symbols)
            wrap.Children.Add(CreateSymbolButton(entry));

        parent.Children.Add(wrap);
    }

    private Button CreateSymbolButton(SymbolEntry entry)
    {
        // Native ``Button`` gives the right defaults for free: hover state,
        // pressed state, keyboard accessibility, system cursor change.  We
        // just override sizing/padding/font for a chip-like appearance.
        //
        // The button face shows ``entry.DisplayLabel`` — either the entry's
        // explicit ``Label`` (used when the paste-text can't be shown, e.g.
        // the non-rendering newline glyph ``↵``) or, when no Label was
        // given, the paste-text with newlines collapsed to a small ``↵``.
        // Click reads the raw ``entry.Text`` for the actual paste, so the
        // payload is always exactly what the entry specified.
        var button = new Button
        {
            Content = entry.DisplayLabel,
            MinWidth = 54,
            MinHeight = 42,
            Padding = new Thickness(12, 4, 12, 4),
            CornerRadius = new CornerRadius(9),
            FontSize = 18,
            HorizontalContentAlignment = HorizontalAlignment.Center,
            VerticalContentAlignment = VerticalAlignment.Center,
            Tag = entry,
        };

        button.Click += SymbolButton_Click;
        button.PointerEntered += SymbolButton_PointerEntered;
        button.PointerExited += SymbolButton_PointerExited;

        return button;
    }

    /// <summary>
    /// Paste a ready-made payload into the previously active window, via
    /// the same path the symbol buttons use. Shared by the formula and
    /// table tabs.
    /// </summary>
    private async void InsertExternal(string payload)
    {
        if (string.IsNullOrEmpty(payload))
            return;

        IntPtr target = _focusService.LastExternalWindow;
        try
        {
            await _clipboardPasteService.AutoPasteTextAsync(
                payload, target, restoreTextClipboard: true);
        }
        catch (Exception ex)
        {
            ClipboardPasteService.CopyText(payload);
            StatusText.Text = "Copy only: " + ex.Message;
        }
    }

    private async void SymbolButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not SymbolEntry entry)
            return;

        string symbol = entry.Text;
        if (string.IsNullOrWhiteSpace(symbol))
            return;

        IntPtr target = _focusService.LastExternalWindow;

        try
        {
            await _clipboardPasteService.AutoPasteTextAsync(
                symbol,
                target,
                restoreTextClipboard: true);
            // Silent on success — the button's own visual press feedback is
            // confirmation enough.  The status bar stays on whatever the
            // current hover description is, so the engineer can keep
            // reading hints while clicking.
        }
        catch (Exception ex)
        {
            ClipboardPasteService.CopyText(symbol);
            StatusText.Text = "Copy only: " + ex.Message;
        }
    }

    /// <summary>
    /// Append the Markdown table builder as one more tab, after the
    /// formula tab. Like the formula editor it is a live control, not a
    /// generated panel of symbol buttons, so it goes straight into the
    /// parallel tab lists.
    /// </summary>
    private void AppendMarkdownTableTab()
    {
        var builder = new Controls.MarkdownTableBuilder();

        builder.SetBinding(FrameworkElement.HeightProperty,
            new Microsoft.UI.Xaml.Data.Binding
            {
                Source = TabContentHost,
                Path = new PropertyPath("ViewportHeight"),
            });

        builder.InsertRequested += (_, payload) => InsertExternal(payload);

        _tabContents.Add(builder);

        var tabButton = new ToggleButton
        {
            Content = MakeTabTextFace("\u25A6"),   // ▦
            Tag = _tabButtons.Count,
            MinWidth = 0,
            Padding = new Thickness(8, 6, 8, 6),
        };
        tabButton.Click += TabButton_Click;
        tabButton.PointerEntered += TabButton_PointerEntered;
        tabButton.PointerExited += TabButton_PointerExited;
        ToolTipService.SetToolTip(tabButton, "Markdown table");

        _tabButtons.Add(tabButton);
        TabSelector.Children.Add(tabButton);
    }

    private void SymbolButton_PointerEntered(object sender, PointerRoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not SymbolEntry entry)
            return;

        // Prefer the longer ``Detail`` explanation when the entry has one;
        // otherwise fall back to the short ``Description``; otherwise show
        // nothing (the symbol is its own label and a redundant echo would
        // just be noise).
        StatusText.Text = entry.Detail ?? entry.Description ?? string.Empty;
    }

    private void SymbolButton_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        // Clear back to empty when the pointer leaves a button.  Brief
        // flicker is fine — moving between adjacent buttons is fast enough
        // that the next ``Entered`` event takes over before the user
        // perceives a blank state.
        StatusText.Text = string.Empty;
    }

    private void RestoreActiveTab()
    {
        if (_tabContents.Count == 0)
            return;

        int index = Clamp(_settings.ActiveTabIndex, 0, _tabContents.Count - 1);
        SelectTab(index);
    }

    private void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        SaveSettings();

        // Release the single-instance lock and remove the handle file,
        // so the next launch starts a fresh instance cleanly rather than
        // trying to surface this now-closed window.
        if (Application.Current is App app)
            app.SingleInstance.Dispose();
    }

    private void SaveSettings()
    {
        if (_appWindow is null)
            return;

        _settings.HasWindowPlacement = true;
        _settings.WindowX = _appWindow.Position.X;
        _settings.WindowY = _appWindow.Position.Y;
        _settings.WindowWidth = _appWindow.Size.Width;
        _settings.WindowHeight = _appWindow.Size.Height;
        _settings.ActiveTabIndex = GetActiveTabIndex();

        _settingsService.Save(_settings);
    }

    private int GetActiveTabIndex()
    {
        // The active index is tracked directly by SelectTab; clamp to a
        // valid range as a guard for the (not expected) case where no
        // tab has been selected yet.
        return _activeTabIndex < 0 ? 0 : _activeTabIndex;
    }

    private static bool Intersects(RectInt32 a, RectInt32 b)
    {
        return a.X < b.X + b.Width &&
               a.X + a.Width > b.X &&
               a.Y < b.Y + b.Height &&
               a.Y + a.Height > b.Y;
    }

    private static int Clamp(int value, int minimum, int maximum)
        => Math.Min(Math.Max(value, minimum), maximum);
}
