using Microsoft.UI.Dispatching;

namespace SymbolPaletteWinUI.Services;

public sealed class WindowFocusService
{
    private IntPtr _ownHwnd;
    private DispatcherQueueTimer? _timer;
    private readonly uint _ownProcessId = (uint)Environment.ProcessId;

    public IntPtr LastExternalWindow { get; private set; }

    public void Start(IntPtr ownHwnd, DispatcherQueue dispatcherQueue)
    {
        _ownHwnd = ownHwnd;

        _timer = dispatcherQueue.CreateTimer();
        _timer.Interval = TimeSpan.FromMilliseconds(150);
        _timer.Tick += (_, _) => PollForegroundWindow();
        _timer.Start();
    }

    private void PollForegroundWindow()
    {
        IntPtr hwnd = NativeMethods.GetForegroundWindow();
        if (hwnd == IntPtr.Zero || hwnd == _ownHwnd)
            return;

        NativeMethods.GetWindowThreadProcessId(hwnd, out uint processId);
        if (processId == _ownProcessId)
            return;

        LastExternalWindow = hwnd;
    }
}
