using System.Globalization;

namespace SymbolPaletteWinUI.Services;

/// <summary>
/// Enforces a single running palette window.
///
/// The palette is launched frequently — a hotkey, a taskbar pin, a Run
/// box — and an engineer will not always remember it is already open.
/// Without this, each launch spawns another always-on-top window and
/// they stack up.  This service makes the SECOND (and later) launch a
/// no-op for the user: instead of opening another window it surfaces
/// the one already running, then exits.
///
/// Mechanism — two pieces, both deliberately low-tech so there is no
/// dependency on packaged-app identity (this app is unpackaged,
/// ``WindowsPackageType=None``):
///
///   1. A named <see cref="Mutex"/> in the ``Global\`` namespace is the
///      presence flag.  The first process creates and owns it; a later
///      process finds it already exists and therefore knows an instance
///      is live.
///
///   2. The owning process writes its main-window handle (HWND) to a
///      small text file in the app's LocalAppData folder — the same
///      folder <see cref="PaletteSettingsService"/> uses.  A later
///      process reads that handle and asks the window manager to bring
///      it to the front (via <see cref="NativeMethods.BringWindowToFront"/>).
///
/// A file is used for the hand-off rather than a pipe or a broadcast
/// window-message because it needs no message loop on either side and
/// no fragile WinUI window-class lookup — the new process simply reads
/// a number and is done.  A stale handle (previous instance crashed
/// without cleanup) is harmless: ``BringWindowToFront`` on a dead HWND
/// is a silent no-op, and the new process then continues to start
/// normally because the mutex it just failed to find... see
/// <see cref="ShouldYieldToExistingInstance"/> for the exact ordering.
/// </summary>
public sealed class SingleInstanceService : IDisposable
{
    // Mutex name.  ``Global\`` so it spans terminal-server sessions;
    // the GUID-ish suffix keeps it from colliding with anything else.
    private const string MutexName =
        @"Global\SymbolPaletteWinUI_SingleInstance_8F3A1C2D";

    private static string HandleDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SymbolPaletteWinUI");

    private static string HandleFilePath =>
        Path.Combine(HandleDirectory, "instance.hwnd");

    private Mutex? _mutex;
    private bool _ownsMutex;

    /// <summary>
    /// Call once at startup, before creating the main window.
    ///
    /// Returns <c>true</c> when THIS process should yield — an instance
    /// is already running and has been surfaced, so the caller should
    /// not create a window and should exit.  Returns <c>false</c> when
    /// this process is the first/only instance and should start
    /// normally; in that case this service has taken ownership of the
    /// mutex and the caller must later call <see cref="PublishWindow"/>.
    /// </summary>
    public bool ShouldYieldToExistingInstance()
    {
        // ``createdNew`` is false when the mutex already existed — i.e.
        // another instance owns it.  We still construct the Mutex object
        // either way; when we are the owner we keep it for the process
        // lifetime, when we are not we dispose it below.
        _mutex = new Mutex(initiallyOwned: true, MutexName, out bool createdNew);
        _ownsMutex = createdNew;

        if (createdNew)
            return false;   // first instance — start normally.

        // Another instance is live.  Surface its window, then yield.
        TrySurfaceExistingWindow();

        _mutex.Dispose();
        _mutex = null;
        return true;
    }

    /// <summary>
    /// Called by the owning instance once its main window exists, to
    /// record the window handle for any future second launch to find.
    /// Safe to call more than once (the file is simply overwritten).
    /// </summary>
    public void PublishWindow(IntPtr hwnd)
    {
        if (!_ownsMutex)
            return;
        try
        {
            Directory.CreateDirectory(HandleDirectory);
            File.WriteAllText(
                HandleFilePath,
                hwnd.ToInt64().ToString(CultureInfo.InvariantCulture));
        }
        catch
        {
            // The hand-off file is best-effort.  If it cannot be written
            // the only consequence is that a future second launch opens
            // its own window instead of surfacing this one — degraded,
            // not broken.
        }
    }

    private static void TrySurfaceExistingWindow()
    {
        try
        {
            if (!File.Exists(HandleFilePath))
                return;

            string raw = File.ReadAllText(HandleFilePath).Trim();
            if (long.TryParse(raw, NumberStyles.Integer,
                              CultureInfo.InvariantCulture, out long handle)
                && handle != 0)
            {
                // BringWindowToFront restores a minimised window and
                // raises it.  On a stale handle this is a harmless
                // no-op — the user just sees nothing, and since this
                // process is exiting anyway there is no bad state.
                NativeMethods.BringWindowToFront(new IntPtr(handle));
            }
        }
        catch
        {
            // A surface attempt must never throw into startup.  Worst
            // case the existing window is not raised; the second
            // process still exits, so no duplicate window appears.
        }
    }

    /// <summary>
    /// Releases the mutex (owning instance only) and removes the
    /// hand-off file.  Call on application exit.
    /// </summary>
    public void Dispose()
    {
        if (_mutex is not null)
        {
            if (_ownsMutex)
            {
                try { _mutex.ReleaseMutex(); } catch { /* not held */ }

                try
                {
                    if (File.Exists(HandleFilePath))
                        File.Delete(HandleFilePath);
                }
                catch
                {
                    // Leaving a stale handle file is harmless — the next
                    // launch validates the handle before using it.
                }
            }

            _mutex.Dispose();
            _mutex = null;
        }
    }
}
