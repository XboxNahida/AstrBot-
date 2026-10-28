using System.Drawing;
using System.Windows.Forms;

internal sealed class LauncherForm : Form
{
    private readonly Label statusLabel = new();
    private readonly Label messageLabel = new();
    private readonly Button startButton = new();
    private readonly Button stopButton = new();
    private readonly Button panelButton = new();
    private readonly CheckBox autoStartCheckBox = new();
    private bool suppressAutoStartEvent;
    private bool allowClose;

    public event EventHandler? StartRequested;
    public event EventHandler? StopRequested;
    public event EventHandler? ControlPanelRequested;
    public event Action<bool>? AutoStartChanged;

    public LauncherForm()
    {
        Text = "AstrBot 启动器";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(460, 260);
        MinimumSize = new Size(460, 260);
        MaximizeBox = false;
        FormBorderStyle = FormBorderStyle.FixedSingle;

        var title = new Label { Text = "AstrBot 启动器", Dock = DockStyle.Fill, Font = new Font(Font.FontFamily, 16, FontStyle.Bold), TextAlign = ContentAlignment.MiddleLeft };
        statusLabel.Text = "状态：未运行";
        statusLabel.AutoSize = true;
        statusLabel.Font = new Font(Font.FontFamily, 11, FontStyle.Bold);
        messageLabel.Text = "准备就绪。关闭窗口会隐藏到系统托盘。";
        messageLabel.AutoEllipsis = true;
        messageLabel.Dock = DockStyle.Fill;

        startButton.Text = "启动 AstrBot";
        startButton.AutoSize = true;
        startButton.Click += (_, _) => StartRequested?.Invoke(this, EventArgs.Empty);
        stopButton.Text = "关闭 AstrBot";
        stopButton.AutoSize = true;
        stopButton.Click += (_, _) => StopRequested?.Invoke(this, EventArgs.Empty);
        panelButton.Text = "打开控制面板";
        panelButton.AutoSize = true;
        panelButton.Click += (_, _) => ControlPanelRequested?.Invoke(this, EventArgs.Empty);
        autoStartCheckBox.Text = "开机自启动并启动机器人（登录后驻留托盘）";
        autoStartCheckBox.AutoSize = true;
        autoStartCheckBox.CheckedChanged += (_, _) =>
        {
            if (!suppressAutoStartEvent) AutoStartChanged?.Invoke(autoStartCheckBox.Checked);
        };

        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(24), ColumnCount = 1, RowCount = 5 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 32));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.Controls.Add(title, 0, 0);
        layout.Controls.Add(statusLabel, 0, 1);
        layout.Controls.Add(messageLabel, 0, 2);
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, AutoSize = false };
        buttons.Controls.Add(startButton);
        buttons.Controls.Add(stopButton);
        buttons.Controls.Add(panelButton);
        layout.Controls.Add(buttons, 0, 3);
        layout.Controls.Add(autoStartCheckBox, 0, 4);
        Controls.Add(layout);
    }

    public void SetStatus(string status)
    {
        statusLabel.Text = $"状态：{status}";
        startButton.Enabled = status is not ("启动中" or "运行中");
        stopButton.Enabled = status is "运行中" or "停止中";
    }

    public void SetAutoStart(bool enabled)
    {
        suppressAutoStartEvent = true;
        autoStartCheckBox.Checked = enabled;
        suppressAutoStartEvent = false;
    }

    public void ShowInfo(string message)
    {
        messageLabel.ForeColor = SystemColors.ControlText;
        messageLabel.Text = message;
    }

    public void ShowError(string message)
    {
        messageLabel.ForeColor = Color.Firebrick;
        messageLabel.Text = message;
    }

    public void AllowClose() => allowClose = true;

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!allowClose && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            Hide();
            return;
        }
        base.OnFormClosing(e);
    }
}
