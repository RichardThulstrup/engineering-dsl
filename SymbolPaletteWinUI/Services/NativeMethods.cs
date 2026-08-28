using System.ComponentModel;
using System.Runtime.InteropServices;
using Windows.Graphics;

namespace SymbolPaletteWinUI.Services;

internal static class NativeMethods
{
    private const int SW_RESTORE = 9;

    private const int SM_XVIRTUALSCREEN = 76;
    private const int SM_YVIRTUALSCREEN = 77;
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;

    private static readonly IntPtr HWND_TOPMOST = new(-1);

    private const uint SWP_NOSIZE = 0x0001;
    private const uint SWP_NOMOVE = 0x0002;
    private const uint SWP_NOACTIVATE = 0x0010;

    private const uint INPUT_KEYBOARD = 1;

    private const ushort VK_CONTROL = 0x11;
    private const ushort VK_V = 0x56;

    private const uint KEYEVENTF_KEYUP = 0x0002;
    private const uint KEYEVENTF_UNICODE = 0x0004;
    private const uint KEYEVENTF_SCANCODE = 0x0008;

    // Physical scan codes for left Ctrl and V on the standard PC keyboard.
    private const ushort SC_LEFT_CTRL = 0x1D;
    private const ushort SC_V = 0x2F;

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);

    [DllImport("user32.dll")]
    internal static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    internal static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

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
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    internal static RectInt32 GetVirtualScreenBounds()
    {
        return new RectInt32(
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN),
            GetSystemMetrics(SM_CYVIRTUALSCREEN));
    }

    internal static void SetAlwaysOnTop(IntPtr hwnd, bool enabled)
    {
        if (!enabled || hwnd == IntPtr.Zero)
            return;

        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    }

    internal static void BringWindowToFront(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
            return;

        if (IsIconic(hwnd))
            ShowWindow(hwnd, SW_RESTORE);

        SetForegroundWindow(hwnd);
    }

    internal static void SendCtrlVScanCode()
    {
        INPUT[] inputs =
        {
            ScanKeyDown(SC_LEFT_CTRL),
            ScanKeyDown(SC_V),
            ScanKeyUp(SC_V),
            ScanKeyUp(SC_LEFT_CTRL),
        };

        SendKeyboardInputsOrThrow(inputs, "Ctrl+V scancode");
    }

    private const ushort VK_MENU = 0x12;       // Alt
    private const ushort VK_OEM_PLUS = 0xBB;   // '=' / '+' key

    /// <summary>
    /// Send Alt+= via virtual keys. In Word this opens an inline equation
    /// field at the current cursor; combined with a subsequent LaTeX paste,
    /// Word converts the LaTeX into a typeset equation (requires Word 2016+
    /// with the equation editor set to LaTeX mode).
    /// </summary>
    internal static void SendAltEquals()
    {
        INPUT[] inputs =
        {
        KeyDown(VK_MENU),
        KeyDown(VK_OEM_PLUS),
        KeyUp(VK_OEM_PLUS),
        KeyUp(VK_MENU),
    };
        SendKeyboardInputsOrThrow(inputs, "Alt+= virtual-key");
    }

    /// <summary>
    /// True when the given window belongs to Microsoft Word (WINWORD.EXE).
    /// Used by the formula-insert path to switch to the Alt+= + LaTeX flow
    /// instead of the Jupyter-style dollar-delimited paste.
    /// </summary>
    internal static bool IsWordWindow(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
            return false;

        GetWindowThreadProcessId(hwnd, out uint pid);
        try
        {
            using var p = System.Diagnostics.Process.GetProcessById((int)pid);
            return string.Equals(p.ProcessName, "WINWORD",
                StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    internal static void SendCtrlVVirtualKeys()
    {
        INPUT[] inputs =
        {
            KeyDown(VK_CONTROL),
            KeyDown(VK_V),
            KeyUp(VK_V),
            KeyUp(VK_CONTROL),
        };

        SendKeyboardInputsOrThrow(inputs, "Ctrl+V virtual-key");
    }

    internal static void SendUnicodeText(string text)
    {
        var inputs = new List<INPUT>(text.Length * 2);

        // Iterate UTF-16 code units. This intentionally sends surrogate pairs as two units,
        // which is what KEYEVENTF_UNICODE expects for non-BMP characters.
        foreach (char ch in text)
        {
            inputs.Add(UnicodeKey(ch, keyUp: false));
            inputs.Add(UnicodeKey(ch, keyUp: true));
        }

        if (inputs.Count > 0)
            SendKeyboardInputsOrThrow(inputs.ToArray(), "Unicode text");
    }

    private static void SendKeyboardInputsOrThrow(INPUT[] inputs, string description)
    {
        int inputSize = Marshal.SizeOf<INPUT>();
        uint sent = SendInput((uint)inputs.Length, inputs, inputSize);

        if (sent == inputs.Length)
            return;

        int error = Marshal.GetLastWin32Error();
        string errorText;

        if (error != 0)
        {
            try
            {
                errorText = new Win32Exception(error).Message;
            }
            catch
            {
                errorText = "unknown error";
            }
        }
        else
        {
            errorText = "no extended error; this can happen when UIPI/security policy blocks SendInput";
        }

        throw new InvalidOperationException(
            $"SendInput failed for {description}. Sent {sent}/{inputs.Length}. " +
            $"INPUT size={inputSize}. LastError={error}: {errorText}");
    }

    private static INPUT KeyDown(ushort virtualKey) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = virtualKey,
                wScan = 0,
                dwFlags = 0,
                time = 0,
                dwExtraInfo = IntPtr.Zero
            }
        }
    };

    private static INPUT KeyUp(ushort virtualKey) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = virtualKey,
                wScan = 0,
                dwFlags = KEYEVENTF_KEYUP,
                time = 0,
                dwExtraInfo = IntPtr.Zero
            }
        }
    };

    private static INPUT ScanKeyDown(ushort scanCode) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = 0,
                wScan = scanCode,
                dwFlags = KEYEVENTF_SCANCODE,
                time = 0,
                dwExtraInfo = IntPtr.Zero
            }
        }
    };

    private static INPUT ScanKeyUp(ushort scanCode) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = 0,
                wScan = scanCode,
                dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
                time = 0,
                dwExtraInfo = IntPtr.Zero
            }
        }
    };

    private static INPUT UnicodeKey(char ch, bool keyUp) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = 0,
                wScan = ch,
                dwFlags = KEYEVENTF_UNICODE | (keyUp ? KEYEVENTF_KEYUP : 0),
                time = 0,
                dwExtraInfo = IntPtr.Zero
            }
        }
    };

    // Important: INPUT must match the native Win32 INPUT layout. On x64 this is
    // 40 bytes. A common C# bug is to define the union with KEYBDINPUT only; that
    // creates a smaller structure, SendInput receives the wrong cbSize, and the
    // call silently sends 0 events.
    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT
    {
        public uint type;
        public InputUnion U;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)]
        public MOUSEINPUT mi;

        [FieldOffset(0)]
        public KEYBDINPUT ki;

        [FieldOffset(0)]
        public HARDWAREINPUT hi;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HARDWAREINPUT
    {
        public uint uMsg;
        public ushort wParamL;
        public ushort wParamH;
    }
}
