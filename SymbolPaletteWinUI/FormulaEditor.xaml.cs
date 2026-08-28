using System;
using System.IO;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;

namespace SymbolPaletteWinUI.Controls
{

    /// <summary>
    /// Data for <see cref="FormulaEditor.InsertRequested"/>: the bare LaTeX
    /// the user composed, plus whether they chose inline ($...$) or display
    /// ($$...$$). The host decides on the final form — Jupyter wants the
    /// wrappers, Word wants the LaTeX bare inside its equation field.
    /// </summary>
    public sealed class FormulaInsertEventArgs : EventArgs
    {
        public string Latex { get; }
        public bool IsDisplay { get; }

        public FormulaInsertEventArgs(string latex, bool isDisplay)
        {
            Latex = latex;
            IsDisplay = isDisplay;
        }
    }

    /// <summary>
    /// A WYSIWYG math-formula editor. The user composes a formula visually
    /// (MathLive, hosted in a WebView2); on Insert it raises
    /// <see cref="InsertRequested"/> with a ready-to-paste string already
    /// wrapped in $...$ or $$...$$ for a Jupyter markdown cell.
    ///
    /// Drop this control into the main palette window. Subscribe to
    /// InsertRequested and route the payload through the SAME method your
    /// glyph buttons use to insert text into the previously-active window.
    /// </summary>
    public sealed partial class FormulaEditor : UserControl, IKeyboardActivatedTab
    {
        /// <summary>
        /// Raised when the user clicks an Insert button. The string argument
        /// is the final payload — e.g. "$\frac{a}{b}$" — needing no further
        /// processing before it goes to your insert routine.
        /// </summary>
        public event EventHandler<FormulaInsertEventArgs>? InsertRequested;
        public event EventHandler? FieldEntered;
        public event EventHandler? FieldExited;

        private void InsertInline_Click(object sender, RoutedEventArgs e)
            => Emit(isDisplay: false);

        private void InsertDisplay_Click(object sender, RoutedEventArgs e)
            => Emit(isDisplay: true);

        private void Emit(bool isDisplay)
        {
            if (string.IsNullOrWhiteSpace(_currentLatex))
                return;
            InsertRequested?.Invoke(
                this, new FormulaInsertEventArgs(_currentLatex, isDisplay));
        }

        private string _currentLatex = string.Empty;
        private bool _webReady;
        private bool _initialized;

        /// <summary>
        /// Put keyboard focus into the math-field. Called when the formula
        /// tab becomes active so the user can start typing immediately.
        /// </summary>
        public async void FocusInput()
        {
            MathView.Focus(FocusState.Programmatic);

            if (!_webReady)
                return;

            try
            {
                await MathView.CoreWebView2.ExecuteScriptAsync("host.focusField()");
            }
            catch
            {
                // WebView not ready — a click into the field will focus it.
            }
        }

        public FormulaEditor()
        {
            InitializeComponent();
            Loaded += OnLoaded;
        }

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            if (_initialized)
                return;
            _initialized = true;

            try
            {
                await MathView.EnsureCoreWebView2Async();
            }
            catch (Exception ex)
            {
                ShowDiag("WebView2 init failed: " + ex.Message
                    + "  — is the WebView2 Runtime installed?");
                return;
            }

            CoreWebView2 core = MathView.CoreWebView2;

            string assetRoot = Path.Combine(
                AppContext.BaseDirectory, "Assets", "MathEditor");
            string indexPath = Path.Combine(assetRoot, "index.html");
            string scriptPath = Path.Combine(assetRoot, "mathlive", "mathlive.js");

            if (!File.Exists(indexPath))
            {
                ShowDiag("Not found: " + indexPath
                    + "  — the Assets\\MathEditor files were not copied to the "
                    + "build output. The csproj <Content> include is not working.");
                return;
            }
            if (!File.Exists(scriptPath))
            {
                ShowDiag("Not found: " + scriptPath
                    + "  — mathlive.js is missing from Assets\\MathEditor\\mathlive.");
                return;
            }

            core.SetVirtualHostNameToFolderMapping(
                "formula.local", assetRoot,
                CoreWebView2HostResourceAccessKind.Allow);

            core.WebMessageReceived += OnWebMessageReceived;

            core.NavigationCompleted += (_, args) =>
            {
                if (!args.IsSuccess)
                    ShowDiag("Navigation failed: " + args.WebErrorStatus);
            };

            // TEMPORARY (revert to false once it works): lets you right-click
            // the editor and choose Inspect to see the JavaScript console.
            core.Settings.AreDevToolsEnabled = true;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.Settings.IsZoomControlEnabled = false;

            MathView.Source = new Uri("https://formula.local/index.html");
        }

        private void ShowDiag(string message)
        {
            LatexPreview.TextWrapping = TextWrapping.Wrap;  // override the NoWrap
            LatexPreview.Opacity = 1.0;
            LatexPreview.Text = message;
            System.Diagnostics.Debug.WriteLine("[FormulaEditor] " + message);
        }
        private void OnWebMessageReceived(
            CoreWebView2 sender,
            CoreWebView2WebMessageReceivedEventArgs args)
        {
            string json;
            try
            {
                json = args.TryGetWebMessageAsString();
            }
            catch (ArgumentException)
            {
                return; // not a string message
            }
            if (string.IsNullOrEmpty(json)) return;

            try
            {
                using JsonDocument doc = JsonDocument.Parse(json);
                JsonElement root = doc.RootElement;
                string type = root.TryGetProperty("type", out JsonElement t)
                    ? t.GetString() ?? string.Empty
                    : string.Empty;

                switch (type)
                {
                    case "ready":
                        _webReady = true;
                        break;

                    case "change":
                        _currentLatex =
                            root.TryGetProperty("latex", out JsonElement l)
                                ? l.GetString() ?? string.Empty
                                : string.Empty;
                        UpdateState();
                        break;
                    case "fieldEnter":
                        FieldEntered?.Invoke(this, EventArgs.Empty);
                        break;

                    case "fieldExit":
                        FieldExited?.Invoke(this, EventArgs.Empty);
                        break;


                }
            }
            catch (JsonException)
            {
                // Ignore anything that is not the expected message shape.
            }
        }

        private void UpdateState()
        {
            bool hasContent = !string.IsNullOrWhiteSpace(_currentLatex);
            InsertInlineButton.IsEnabled = hasContent;
            InsertDisplayButton.IsEnabled = hasContent;
            LatexPreview.Text = hasContent
                ? "will insert:  " + _currentLatex
                : string.Empty;
        }

        private async void Clear_Click(object sender, RoutedEventArgs e)
        {
            if (_webReady)
                await MathView.CoreWebView2.ExecuteScriptAsync("host.clear()");
        }

        private async void KeyboardToggle_Checked(
            object sender, RoutedEventArgs e)
        {
            if (_webReady)
                await MathView.CoreWebView2
                    .ExecuteScriptAsync("host.showKeyboard()");
        }

        private async void KeyboardToggle_Unchecked(
            object sender, RoutedEventArgs e)
        {
            if (_webReady)
                await MathView.CoreWebView2
                    .ExecuteScriptAsync("host.hideKeyboard()");
        }

        /// <summary>
        /// Optionally preload a LaTeX formula for editing (round-trip).
        /// No-ops if the editor has not finished loading yet.
        /// </summary>
        public async void LoadFormula(string latex)
        {
            if (!_webReady) return;
            string js = "host.setValue("
                        + JsonSerializer.Serialize(latex ?? string.Empty)
                        + ")";
            await MathView.CoreWebView2.ExecuteScriptAsync(js);
        }
    }
}
