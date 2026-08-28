# SymbolPaletteWinUI

A WinUI 3 / Windows App SDK conversion of the Python Symbol Palette.

## Build

1. Install Visual Studio with the Windows App SDK / WinUI workload.
2. Open `SymbolPaletteWinUI.csproj`.
3. Restore NuGet packages.
4. Build for x64.

The project is configured as an unpackaged desktop EXE using Windows App SDK 1.8.260416003.

## Current behavior

This polished version removes the debugging insertion toggles from the UI.

The app now always uses the working insertion path:

1. copy the selected symbol to the Win32 Unicode-text clipboard;
2. keep the palette non-activating so the previous app remains the intended target;
3. send Ctrl+V automatically;
4. restore the previous text clipboard after a delay.

## Remembered UI state

On close, the app stores these values in:

`%LOCALAPPDATA%\SymbolPaletteWinUI\settings.json`

Stored values:

- window/tool-palette size;
- window/tool-palette placement;
- active tab index.

If the saved window position is no longer visible after a monitor-layout change, the app keeps the saved size but lets Windows choose a visible startup position.

## Notes

- Run the palette at the same privilege level as the target application.
- Secure Windows surfaces such as UAC prompts and some password fields will not accept injected input.
- Clipboard restoration preserves text only, not images/files/rich clipboard formats.
