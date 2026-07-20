using System.Windows;

namespace MyBioTools.Windows;

public partial class AccountWindow : Window
{
    private sealed record DeviceItem(DeviceProfile Device)
    {
        public string DisplayName => $"{Device.DeviceName}{(Device.Current ? "（当前设备）" : "")}  ·  {Device.Platform}  ·  {Device.AppVersion}"
            + (Device.RevokedAt is null ? "" : "  ·  已解绑");
    }

    public string? DeviceToRevoke { get; private set; }
    public bool LogoutRequested { get; private set; }
    public bool PasswordResetRequested { get; private set; }

    internal AccountWindow(UserProfile? user, IEnumerable<DeviceProfile> devices, DateTimeOffset? offlineExpiresAt, bool offline)
    {
        InitializeComponent();
        NameText.Text = user?.RealName ?? "当前账号";
        EmailText.Text = user?.Email ?? string.Empty;
        var accountAuthorization = user?.AuthorizationExpiresAt is long timestamp
            ? $"账号授权至 {DateTimeOffset.FromUnixTimeSeconds(timestamp).ToLocalTime():yyyy-MM-dd HH:mm}"
            : user?.AuthorizationPermanent == true ? "账号授权：永久" : "账号授权：未读取，请联网刷新";
        var offlineLicense = offlineExpiresAt is null
            ? "本机离线凭证：未读取"
            : $"本机离线凭证至 {offlineExpiresAt.Value.ToLocalTime():yyyy-MM-dd HH:mm}{(offline ? "（离线模式）" : "")}";
        LicenseText.Text = $"{accountAuthorization}\n{offlineLicense}";
        DevicesList.ItemsSource = devices.Select(device => new DeviceItem(device)).ToArray();
    }

    private void UnbindButton_Click(object sender, RoutedEventArgs e)
    {
        if (DevicesList.SelectedItem is not DeviceItem item || item.Device.RevokedAt is not null)
        {
            MessageBox.Show(this, "请先选择一台未解绑的设备。", "设备解绑", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        var warning = item.Device.Current
            ? "这是当前设备。解绑后将立即退出并停止分析服务。"
            : "解绑后，该设备已签发的会话将失效。";
        if (MessageBox.Show(this, warning + "\n\n确认继续？", "确认解绑", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        DeviceToRevoke = item.Device.Id;
        DialogResult = true;
    }

    private void LogoutButton_Click(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(this, "退出后将立即停止本机分析服务。", "确认退出", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        LogoutRequested = true;
        DialogResult = true;
    }

    private void ChangePasswordButton_Click(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(this, "系统将向当前账号邮箱发送 30 分钟有效的密码重置链接。", "修改密码", MessageBoxButton.OKCancel, MessageBoxImage.Information) != MessageBoxResult.OK) return;
        PasswordResetRequested = true;
        DialogResult = true;
    }
}
