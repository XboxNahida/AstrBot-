using Microsoft.Win32;

internal static class StartupManager
{
    private const string RunKeyPath = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
    private const string ValueName = "AstrBot启动器";

    public static bool IsEnabled()
    {
        using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
        var value = key?.GetValue(ValueName) as string;
        return value is not null && value.Contains(Application.ExecutablePath, StringComparison.OrdinalIgnoreCase);
    }

    public static void SetEnabled(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath);
        if (key is null) throw new InvalidOperationException("无法打开当前用户的开机启动项。");
        if (enabled) key.SetValue(ValueName, $"\"{Application.ExecutablePath}\" --tray --start-bot");
        else key.DeleteValue(ValueName, throwOnMissingValue: false);
    }
}
