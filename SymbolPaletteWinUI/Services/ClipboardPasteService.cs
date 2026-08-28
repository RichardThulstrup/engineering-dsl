namespace SymbolPaletteWinUI.Services;

public sealed class ClipboardPasteService
{
    public async Task AutoPasteTextAsync(string text, IntPtr targetWindow, bool restoreTextClipboard)
    {
        if (string.IsNullOrEmpty(text))
            return;

        // Save only text clipboard contents. Rich/image/file clipboard data is intentionally
        // not restored by this minimal implementation. Keep restore OFF while debugging.
        string? previousText = restoreTextClipboard
            ? await Win32ClipboardService.TryGetTextWithRetryAsync()
            : null;

        // Use the classic Win32 clipboard rather than WinUI DataPackage here.
        // It makes CF_UNICODETEXT available immediately to Win32 edit controls such as Notepad.
        await Win32ClipboardService.SetTextWithRetryAsync(text);

        bool confirmed = await Win32ClipboardService.WaitUntilTextEqualsAsync(text, timeoutMs: 1000);
        if (!confirmed)
            throw new InvalidOperationException("Symbol was not visible on the clipboard before paste was sent.");

        if (targetWindow == IntPtr.Zero)
            return;

        // The palette should be WS_EX_NOACTIVATE, so the previous app should still be foreground.
        // BringWindowToFront remains a safety net.
        await Task.Delay(120);
        NativeMethods.BringWindowToFront(targetWindow);
        await Task.Delay(250);

        // Virtual-key Ctrl+V is normally the best representation of the Windows paste accelerator.
        NativeMethods.SendCtrlVVirtualKeys();

        // Do not restore too early. Some applications process Ctrl+V asynchronously, and if
        // the clipboard is restored immediately they paste the old clipboard instead of the symbol.
        if (restoreTextClipboard && previousText is not null)
        {
            await Task.Delay(1500);
            await Win32ClipboardService.SetTextWithRetryAsync(previousText);
        }
    }

    public static async Task CopyTextAsync(string text)
    {
        await Win32ClipboardService.SetTextWithRetryAsync(text);
    }

    public static void CopyText(string text)
    {
        // Compatibility wrapper for existing catch blocks.
        Win32ClipboardService.SetTextWithRetryAsync(text).GetAwaiter().GetResult();
    }
}
