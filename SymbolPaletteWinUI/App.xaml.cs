using Microsoft.UI.Xaml;
using SymbolPaletteWinUI.Services;

namespace SymbolPaletteWinUI;

public partial class App : Application
{
    private Window? _window;

    // Held for the process lifetime by the FIRST instance; surfaces the
    // existing window (and then yields) for any later instance.  Exposed
    // so MainWindow can publish its handle once created and release on
    // close.
    private readonly SingleInstanceService _singleInstance = new();

    public App()
    {
        InitializeComponent();
    }

    /// <summary>
    /// The process-wide single-instance coordinator.  MainWindow uses
    /// this to publish its window handle once it exists, and to release
    /// the instance lock when it closes.
    /// </summary>
    public SingleInstanceService SingleInstance => _singleInstance;

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // Single-instance gate.  If a palette is already running, its
        // window has just been brought to the front by the service —
        // this process must NOT create a second window.  WinUI has no
        // direct "abort launch" call, so we simply return without
        // creating or activating a window and let the process fall
        // idle; Exit() asks the runtime to shut it down.
        if (_singleInstance.ShouldYieldToExistingInstance())
        {
            Exit();
            return;
        }

        _window = new MainWindow();
        _window.Activate();
    }
}