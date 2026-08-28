using System.Runtime.InteropServices;

namespace SymbolPaletteWinUI.Services;

/// <summary>
/// Makes the WinUI top-level HWND behave like a real floating palette:
/// mouse clicks are delivered to the palette controls, but the palette itself
/// does not become the foreground window. This is essential because the paste
/// shortcut must still go to the editor/application that was active before the
/// user clicked a symbol.
/// </summary>
internal sealed class NoActivateWindowService
{
    private const int GWL_EXSTYLE = -20;
    private const int GWL_WNDPROC = -4;

    private const int WS_EX_TOOLWINDOW = 0x00000080;
    private const int WS_EX_NOACTIVATE = 0x08000000;

    private const uint WM_MOUSEACTIVATE = 0x0021;
    private static readonly IntPtr MA_NOACTIVATE = new(3);

    private const uint SWP_NOSIZE = 0x0001;
    private const uint SWP_NOMOVE = 0x0002;
    private const uint SWP_NOZORDER = 0x0004;
    private const uint SWP_NOACTIVATE = 0x0010;
    private const uint SWP_FRAMECHANGED = 0x0020;

    private IntPtr _hwnd;
    private IntPtr _oldWndProc;
    private WndProcDelegate? _newWndProc;
    private bool _suppressActivation = true;

    public void Apply(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || _hwnd == hwnd)
            return;

        _hwnd = hwnd;

        IntPtr exStyle = GetWindowLongPtr(hwnd, GWL_EXSTYLE);
        var newExStyle = new IntPtr(exStyle.ToInt64() | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW);
        SetWindowLongPtr(hwnd, GWL_EXSTYLE, newExStyle);

        // Force Windows to re-read the changed extended window style.
        SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);

        // WS_EX_NOACTIVATE prevents normal click activation, but handling
        // WM_MOUSEACTIVATE makes the behavior explicit and robust.
        _newWndProc = WndProc;
        _oldWndProc = SetWindowLongPtr(hwnd, GWL_WNDPROC,
            Marshal.GetFunctionPointerForDelegate(_newWndProc));
    }

    /// <summary>
    /// Switch the palette between non-activating (the default: a click
    /// never steals foreground, so symbol paste lands in the previously
    /// active application) and normally activating (required when a
    /// hosted control such as the formula editor must receive physical
    /// keyboard input). Safe to call before <see cref="Apply"/> — the
    /// flag is remembered and the window style is updated once Apply has
    /// run and the caller invokes this again.
    /// </summary>
    public void SetSuppressActivation(bool suppress)
    {
        _suppressActivation = suppress;

        if (_hwnd == IntPtr.Zero)
            return;

        long exStyle = GetWindowLongPtr(_hwnd, GWL_EXSTYLE).ToInt64();
        long updated = suppress
            ? exStyle | WS_EX_NOACTIVATE
            : exStyle & ~(long)WS_EX_NOACTIVATE;

        if (updated == exStyle)
            return;

        SetWindowLongPtr(_hwnd, GWL_EXSTYLE, new IntPtr(updated));

        // Force Windows to re-read the changed extended style.
        SetWindowPos(_hwnd, IntPtr.Zero, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
    }

    private IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        // Veto click-activation only while suppression is on. The formula
        // tab switches it off so its math-field can receive keystrokes.
        if (msg == WM_MOUSEACTIVATE && _suppressActivation)
            return MA_NOACTIVATE;

        return CallWindowProc(_oldWndProc, hwnd, msg, wParam, lParam);
    }

    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private static IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex)
        => IntPtr.Size == 8
            ? GetWindowLongPtr64(hWnd, nIndex)
            : new IntPtr(GetWindowLong32(hWnd, nIndex));

    private static IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong)
        => IntPtr.Size == 8
            ? SetWindowLongPtr64(hWnd, nIndex, dwNewLong)
            : new IntPtr(SetWindowLong32(hWnd, nIndex, dwNewLong.ToInt32()));

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern int GetWindowLong32(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hWnd, int nIndex, int dwNewLong);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int X,
        int Y,
        int cx,
        int cy,
        uint uFlags);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CallWindowProc(
        IntPtr lpPrevWndFunc,
        IntPtr hWnd,
        uint msg,
        IntPtr wParam,
        IntPtr lParam);
}
