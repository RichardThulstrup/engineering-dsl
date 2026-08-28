using System.Text.Json;

namespace SymbolPaletteWinUI.Services;

public sealed class PaletteSettings
{
    public bool HasWindowPlacement { get; set; }
    public int WindowX { get; set; }
    public int WindowY { get; set; }
    public int WindowWidth { get; set; } = 720;
    public int WindowHeight { get; set; } = 560;
    public int ActiveTabIndex { get; set; }
}

public sealed class PaletteSettingsService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true
    };

    private static string SettingsDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SymbolPaletteWinUI");

    private static string SettingsPath => Path.Combine(SettingsDirectory, "settings.json");

    public PaletteSettings Load()
    {
        try
        {
            if (!File.Exists(SettingsPath))
                return new PaletteSettings();

            string json = File.ReadAllText(SettingsPath);
            return JsonSerializer.Deserialize<PaletteSettings>(json) ?? new PaletteSettings();
        }
        catch
        {
            // Bad/corrupt settings should never prevent the palette from starting.
            return new PaletteSettings();
        }
    }

    public void Save(PaletteSettings settings)
    {
        try
        {
            Directory.CreateDirectory(SettingsDirectory);
            string json = JsonSerializer.Serialize(settings, JsonOptions);
            File.WriteAllText(SettingsPath, json);
        }
        catch
        {
            // Persisting UI state is best-effort only. Ignore failures.
        }
    }
}
