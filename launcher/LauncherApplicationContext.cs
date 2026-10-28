using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

internal sealed class LauncherApplicationContext : ApplicationContext
{
    private readonly AstrBotProcessManager processManager = new();
    private readonly LauncherForm form;
    private readonly NotifyIcon trayIcon;

    public LauncherApplicationContext()
    {
        var arguments = Environment.GetCommandLineArgs();
        var startBotOnLaunch = arguments.Any(argument =>
            string.Equals(argument, "--start-bot", StringComparison.OrdinalIgnoreCase));
        var startInTray = arguments.Any(argument =>
            string.Equals(argument, "--tray", StringComparison.OrdinalIgnoreCase));

        form = new LauncherForm();
        form.StartRequested += (_, _) => StartBot();
        form.StopRequested += async (_, _) => await StopBotAsync();
        form.ControlPanelRequested += (_, _) => OpenControlPanel();
        form.AutoStartChanged += SetAutoStart;
        processManager.StatusChanged += (_, args) => UpdateStatus(args.Status);
        trayIcon = new NotifyIcon { Icon = SystemIcons.Application, Text = "AstrBot 启动器", Visible = true, ContextMenuStrip = CreateTrayMenu() };
        trayIcon.DoubleClick += (_, _) => ShowMainWindow();
        form.SetAutoStart(StartupManager.IsEnabled());
        processManager.Refresh();
        if (!startInTray)
        {
            form.Show();
        }
        if (startBotOnLaunch)
        {
            StartBot();
        }
    }

    private ContextMenuStrip CreateTrayMenu()
    {
        var menu = new ContextMenuStrip();
        menu.Items.Add("打开主界面", null, (_, _) => ShowMainWindow());
        menu.Items.Add("打开控制面板", null, (_, _) => OpenControlPanel());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("启动 AstrBot", null, (_, _) => StartBot());
        menu.Items.Add("关闭 AstrBot", null, async (_, _) => await StopBotAsync());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出启动器", null, (_, _) => ExitApplication());
        return menu;
    }

    private void StartBot()
    {
        if (processManager.Start()) form.ShowInfo("已提交启动请求，正在等待 AstrBot 就绪。");
        else if (processManager.LastError is not null) form.ShowError(processManager.LastError);
    }

    private async Task StopBotAsync()
    {
        var stopped = await processManager.StopAsync(TimeSpan.FromSeconds(10));
        if (!stopped && processManager.LastError is not null) form.ShowError(processManager.LastError);
        else form.ShowInfo("AstrBot 已关闭。");
    }

    private void OpenControlPanel()
    {
        try { Process.Start(new ProcessStartInfo("http://127.0.0.1:6185") { UseShellExecute = true }); }
        catch (Exception ex) { form.ShowError($"无法打开控制面板：{ex.Message}"); }
    }

    private void SetAutoStart(bool enabled)
    {
        try
        {
            StartupManager.SetEnabled(enabled);
            form.ShowInfo(enabled ? "已开启当前用户开机自启动。" : "已关闭当前用户开机自启动。");
        }
        catch (Exception ex)
        {
            form.SetAutoStart(!enabled);
            form.ShowError($"保存开机自启动设置失败：{ex.Message}");
        }
    }

    private void UpdateStatus(string status)
    {
        if (form.IsDisposed) return;
        if (form.InvokeRequired) { form.BeginInvoke(() => UpdateStatus(status)); return; }
        form.SetStatus(status);
    }

    private void ShowMainWindow()
    {
        form.Show();
        form.WindowState = FormWindowState.Normal;
        form.Activate();
    }

    private void ExitApplication()
    {
        form.AllowClose();
        trayIcon.Visible = false;
        trayIcon.Dispose();
        processManager.Dispose();
        form.Close();
        ExitThread();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            trayIcon.Dispose();
            processManager.Dispose();
            form.Dispose();
        }
        base.Dispose(disposing);
    }
}
