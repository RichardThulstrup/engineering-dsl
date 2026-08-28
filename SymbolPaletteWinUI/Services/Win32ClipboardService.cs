using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace SymbolPaletteWinUI.Services;

internal static class Win32ClipboardService
{
    private const uint CF_UNICODETEXT = 13;
    private const uint GMEM_MOVEABLE = 0x0002;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool OpenClipboard(IntPtr hWndNewOwner);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EmptyClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetClipboardData(uint uFormat, IntPtr hMem);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetClipboardData(uint uFormat);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsClipboardFormatAvailable(uint format);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GlobalLock(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GlobalUnlock(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GlobalFree(IntPtr hMem);

    internal static async Task SetTextWithRetryAsync(string text, int attempts = 12, int delayMs = 25)
    {
        Exception? last = null;

        for (int i = 0; i < attempts; i++)
        {
            try
            {
                SetText(text);
                return;
            }
            catch (Exception ex)
            {
                last = ex;
                await Task.Delay(delayMs);
            }
        }

        throw new InvalidOperationException("Could not open/set the Windows clipboard.", last);
    }

    internal static async Task<string?> TryGetTextWithRetryAsync(int attempts = 8, int delayMs = 25)
    {
        for (int i = 0; i < attempts; i++)
        {
            try
            {
                return TryGetText();
            }
            catch
            {
                await Task.Delay(delayMs);
            }
        }

        return null;
    }

    internal static async Task<bool> WaitUntilTextEqualsAsync(string expected, int timeoutMs = 1000)
    {
        long stopAt = Environment.TickCount64 + timeoutMs;

        while (Environment.TickCount64 < stopAt)
        {
            string? current = await TryGetTextWithRetryAsync(attempts: 1, delayMs: 0);
            if (current == expected)
                return true;

            await Task.Delay(20);
        }

        return false;
    }

    private static void SetText(string text)
    {
        IntPtr hGlobal = IntPtr.Zero;
        bool clipboardOpen = false;

        try
        {
            if (!OpenClipboard(IntPtr.Zero))
                throw LastWin32("OpenClipboard failed");

            clipboardOpen = true;

            if (!EmptyClipboard())
                throw LastWin32("EmptyClipboard failed");

            byte[] bytes = Encoding.Unicode.GetBytes(text + "\0");
            hGlobal = GlobalAlloc(GMEM_MOVEABLE, (UIntPtr)bytes.Length);
            if (hGlobal == IntPtr.Zero)
                throw LastWin32("GlobalAlloc failed");

            IntPtr pGlobal = GlobalLock(hGlobal);
            if (pGlobal == IntPtr.Zero)
                throw LastWin32("GlobalLock failed");

            try
            {
                Marshal.Copy(bytes, 0, pGlobal, bytes.Length);
            }
            finally
            {
                GlobalUnlock(hGlobal);
            }

            if (SetClipboardData(CF_UNICODETEXT, hGlobal) == IntPtr.Zero)
                throw LastWin32("SetClipboardData(CF_UNICODETEXT) failed");

            // Ownership has been transferred to the clipboard. Do not free hGlobal.
            hGlobal = IntPtr.Zero;
        }
        finally
        {
            if (clipboardOpen)
                CloseClipboard();

            if (hGlobal != IntPtr.Zero)
                GlobalFree(hGlobal);
        }
    }

    private static string? TryGetText()
    {
        bool clipboardOpen = false;

        try
        {
            if (!OpenClipboard(IntPtr.Zero))
                throw LastWin32("OpenClipboard failed");

            clipboardOpen = true;

            if (!IsClipboardFormatAvailable(CF_UNICODETEXT))
                return null;

            IntPtr hData = GetClipboardData(CF_UNICODETEXT);
            if (hData == IntPtr.Zero)
                return null;

            IntPtr pData = GlobalLock(hData);
            if (pData == IntPtr.Zero)
                return null;

            try
            {
                return Marshal.PtrToStringUni(pData);
            }
            finally
            {
                GlobalUnlock(hData);
            }
        }
        finally
        {
            if (clipboardOpen)
                CloseClipboard();
        }
    }

    private static Win32Exception LastWin32(string message)
    {
        int error = Marshal.GetLastWin32Error();
        return new Win32Exception(error, $"{message}. LastError={error}");
    }
}
