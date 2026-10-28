using System.Diagnostics;
using System.Net.NetworkInformation;

internal sealed class AstrBotProcessManager : IDisposable
{
    private const int AstrBotPort = 6185;
    private readonly string? projectDirectory;
    private Process? process;

    public event EventHandler<ProcessStatusChangedEventArgs>? StatusChanged;
    public string? LastError { get; private set; }
    public bool IsRunning => process is { HasExited: false } || IsPortListening(AstrBotPort);

    public AstrBotProcessManager()
    {
        var launcherDirectory = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        projectDirectory = FindDirectory(launcherDirectory, "AstrBot-master");
    }

    public bool Start()
    {
        LastError = null;
        if (IsRunning)
        {
            LastError = "AstrBot 已经在运行中，未重复启动。";
            RaiseStatus("运行中");
            return false;
        }

        if (projectDirectory is null)
        {
            LastError = "找不到 AstrBot-master 文件夹。请将启动器放在 AstrBot 根目录。";
            RaiseStatus("启动失败");
            return false;
        }

        var pythonPath = Path.Combine(projectDirectory, ".venv", "Scripts", "python.exe");
        var mainPath = Path.Combine(projectDirectory, "main.py");
        if (!File.Exists(pythonPath))
        {
            LastError = $"找不到 Python 虚拟环境：{pythonPath}";
            RaiseStatus("启动失败");
            return false;
        }
        if (!File.Exists(mainPath))
        {
            LastError = $"找不到 AstrBot 主程序：{mainPath}";
            RaiseStatus("启动失败");
            return false;
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = pythonPath,
                WorkingDirectory = projectDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            startInfo.ArgumentList.Add(mainPath);
            var dashboardDirectory = Path.Combine(Path.GetDirectoryName(projectDirectory)!, "AstrBot-v4.27.2-dashboard", "dist");
            if (Directory.Exists(dashboardDirectory))
            {
                startInfo.ArgumentList.Add("--webui-dir");
                startInfo.ArgumentList.Add(dashboardDirectory);
            }

            var certifiPath = Path.Combine(projectDirectory, ".venv", "Lib", "site-packages", "certifi", "cacert.pem");
            if (File.Exists(certifiPath))
            {
                startInfo.Environment["SSL_CERT_FILE"] = certifiPath;
            }

            process = Process.Start(startInfo);
            if (process is null)
            {
                LastError = "无法启动 AstrBot Python 进程。";
                RaiseStatus("启动失败");
                return false;
            }
            process.EnableRaisingEvents = true;
            process.Exited += ManagedProcessExited;
            RaiseStatus("启动中");
            return true;
        }
        catch (Exception ex)
        {
            LastError = $"启动 AstrBot 失败：{ex.Message}";
            RaiseStatus("启动失败");
            return false;
        }
    }

    public async Task<bool> StopAsync(TimeSpan timeout)
    {
        LastError = null;
        var current = process;
        if (current is null || current.HasExited)
        {
            if (IsPortListening(AstrBotPort))
            {
                LastError = "当前 6185 端口由其他 AstrBot 进程占用，启动器未接管它，无法安全关闭。";
                RaiseStatus("运行中");
                return false;
            }
            RaiseStatus("未运行");
            return true;
        }

        RaiseStatus("停止中");
        try
        {
            current.CloseMainWindow();
            using var cancellation = new CancellationTokenSource(timeout);
            try
            {
                await current.WaitForExitAsync(cancellation.Token);
            }
            catch (OperationCanceledException)
            {
                if (!current.HasExited)
                {
                    current.Kill(entireProcessTree: true);
                    await current.WaitForExitAsync();
                }
            }
            RaiseStatus("未运行");
            return true;
        }
        catch (Exception ex)
        {
            LastError = $"关闭 AstrBot 失败：{ex.Message}";
            RaiseStatus("运行中");
            return false;
        }
    }

    public void Refresh() => RaiseStatus(IsRunning ? "运行中" : "未运行");

    public void Dispose()
    {
        if (process is not null)
        {
            process.Exited -= ManagedProcessExited;
            process.Dispose();
            process = null;
        }
    }

    private void ManagedProcessExited(object? sender, EventArgs e) => RaiseStatus(IsPortListening(AstrBotPort) ? "运行中" : "未运行");
    private void RaiseStatus(string status) => StatusChanged?.Invoke(this, new ProcessStatusChangedEventArgs(status));

    private static bool IsPortListening(int port)
    {
        try { return IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners().Any(endpoint => endpoint.Port == port); }
        catch { return false; }
    }

    private static string? FindDirectory(string launcherDirectory, string directoryName)
    {
        var direct = Path.Combine(launcherDirectory, directoryName);
        if (Directory.Exists(direct)) return direct;
        var parent = Directory.GetParent(launcherDirectory)?.FullName;
        var sibling = parent is null ? null : Path.Combine(parent, directoryName);
        return sibling is not null && Directory.Exists(sibling) ? sibling : null;
    }
}

internal sealed class ProcessStatusChangedEventArgs(string status) : EventArgs
{
    public string Status { get; } = status;
}
