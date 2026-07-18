using System.Diagnostics;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using Microsoft.Web.WebView2.Core;

namespace MyBioTools.Windows;

public partial class MainWindow : Window
{
    private const string WebViewInstallerName = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe";
    private readonly BackendController _backend;
    private readonly AuthService _auth = new();
    private readonly System.Windows.Threading.DispatcherTimer _authRefreshTimer;
    private bool _browserInitialized;
    private bool _backendStarted;
    private Uri? _currentBaseUrl;
    private HwndSource? _windowSource;

    public MainWindow()
    {
        InitializeComponent();
        _backend = new BackendController(Dispatcher);
        _backend.SnapshotChanged += Backend_SnapshotChanged;
        _authRefreshTimer = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromHours(6),
        };
        _authRefreshTimer.Tick += AuthRefreshTimer_Tick;
        SourceInitialized += MainWindow_SourceInitialized;
    }

    private async void Window_Loaded(object sender, RoutedEventArgs e)
    {
        ShowPanel(LaunchPanel);
        try
        {
            await _auth.InitializeAsync();
            if (_auth.Authorization is null)
            {
                ShowAuthPanel();
                return;
            }
            await CompleteAuthorizationAsync();
        }
        catch (Exception exception)
        {
            AuthMessageText.Text = exception.Message;
            ShowAuthPanel();
        }
    }

    private void Window_Closed(object? sender, EventArgs e)
    {
        _backend.Dispose();
        _authRefreshTimer.Stop();
        _windowSource?.RemoveHook(WindowMessageHook);
    }

    private async Task StartApplicationAsync()
    {
        ShowPanel(LaunchPanel);
        if (!IsWebViewRuntimeAvailable())
        {
            ShowRuntimePanel();
            return;
        }

        if (_backendStarted)
        {
            return;
        }

        _backendStarted = true;
        await _backend.StartAsync();
    }

    private async Task CompleteAuthorizationAsync()
    {
        if (_auth.Authorization is null)
        {
            ShowAuthPanel();
            return;
        }
        _backend.ConfigureAuthorization(_auth.Authorization);
        AccountEmailText.Text = _auth.User?.Email ?? string.Empty;
        _authRefreshTimer.Start();
        await StartApplicationAsync();
    }

    private void ShowAuthPanel()
    {
        _backend.ConfigureAuthorization(null);
        _backendStarted = false;
        _authRefreshTimer.Stop();
        ShowPanel(AuthPanel);
    }

    private void SetAuthBusy(bool busy)
    {
        AuthProgress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        LoginButton.IsEnabled = !busy;
        RegisterButton.IsEnabled = !busy;
    }

    private async void LoginButton_Click(object sender, RoutedEventArgs e)
    {
        SetAuthBusy(true);
        AuthMessageText.Text = string.Empty;
        try
        {
            await _auth.LoginAsync(LoginEmailBox.Text.Trim(), LoginPasswordBox.Password);
            await CompleteAuthorizationAsync();
        }
        catch (AuthApiException exception)
        {
            AuthMessageText.Text = exception.Code switch
            {
                "EMAIL_UNVERIFIED" => "邮箱尚未验证。请检查验证邮件后再登录。",
                "PENDING_REVIEW" => "邮箱已验证，当前正在等待管理员审核。",
                "ACCOUNT_REJECTED" => $"申请未通过：{exception.Message}",
                "ACCOUNT_SUSPENDED" => $"账号已停用：{exception.Message}",
                "DEVICE_LIMIT_REACHED" => "账号已绑定 2 台设备，请联系管理员或在旧设备上解绑。",
                _ => exception.Message,
            };
        }
        catch (Exception exception)
        {
            AuthMessageText.Text = exception.Message;
        }
        finally
        {
            LoginPasswordBox.Clear();
            SetAuthBusy(false);
        }
    }

    private async void RegisterButton_Click(object sender, RoutedEventArgs e)
    {
        if (RegisterPasswordBox.Password != RegisterConfirmBox.Password)
        {
            AuthMessageText.Text = "两次输入的密码不一致。";
            return;
        }
        if (RegisterPasswordBox.Password.Length < 12)
        {
            AuthMessageText.Text = "密码至少需要 12 个字符。";
            return;
        }
        SetAuthBusy(true);
        try
        {
            var role = (RegisterRoleBox.SelectedItem as System.Windows.Controls.ComboBoxItem)?.Content?.ToString() ?? "其他";
            AuthMessageText.Text = await _auth.RegisterAsync(
                RegisterEmailBox.Text.Trim(), RegisterNameBox.Text.Trim(), role,
                RegisterNoteBox.Text.Trim(), RegisterPasswordBox.Password);
            AuthTabs.SelectedIndex = 0;
            LoginEmailBox.Text = RegisterEmailBox.Text.Trim();
        }
        catch (Exception exception)
        {
            AuthMessageText.Text = exception.Message;
        }
        finally
        {
            RegisterPasswordBox.Clear();
            RegisterConfirmBox.Clear();
            SetAuthBusy(false);
        }
    }

    private async void ForgotPasswordButton_Click(object sender, RoutedEventArgs e)
    {
        SetAuthBusy(true);
        try { AuthMessageText.Text = await _auth.ForgotPasswordAsync(LoginEmailBox.Text.Trim()); }
        catch (Exception exception) { AuthMessageText.Text = exception.Message; }
        finally { SetAuthBusy(false); }
    }

    private async void ResendVerificationButton_Click(object sender, RoutedEventArgs e)
    {
        SetAuthBusy(true);
        try { AuthMessageText.Text = await _auth.ResendVerificationAsync(LoginEmailBox.Text.Trim()); }
        catch (Exception exception) { AuthMessageText.Text = exception.Message; }
        finally { SetAuthBusy(false); }
    }

    private async void AuthRefreshTimer_Tick(object? sender, EventArgs e)
    {
        await RefreshAuthorizationAsync();
    }

    private async Task RefreshAuthorizationAsync()
    {
        try
        {
            await _auth.RefreshAsync();
            if (_auth.Authorization is null) ShowAuthPanel();
            else _backend.ConfigureAuthorization(_auth.Authorization);
        }
        catch (Exception exception)
        {
            AuthMessageText.Text = exception.Message;
            ShowAuthPanel();
        }
    }

    private async void AccountButton_Click(object sender, RoutedEventArgs e)
    {
        var devices = Array.Empty<DeviceProfile>();
        try { devices = await _auth.GetDevicesAsync(); }
        catch (Exception exception) { MessageBox.Show(this, exception.Message, "设备列表加载失败"); }
        var account = new AccountWindow(_auth.User, devices, _auth.LicenseExpiresAt, _auth.IsOffline) { Owner = this };
        if (account.ShowDialog() != true) return;
        if (account.LogoutRequested)
        {
            await _auth.LogoutAsync();
            ShowAuthPanel();
            return;
        }
        if (account.PasswordResetRequested && _auth.User is not null)
        {
            try
            {
                var message = await _auth.ForgotPasswordAsync(_auth.User.Email);
                MessageBox.Show(this, message, "修改密码", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception exception) { MessageBox.Show(this, exception.Message, "邮件发送失败"); }
            return;
        }
        if (account.DeviceToRevoke is not null)
        {
            try
            {
                var current = devices.FirstOrDefault(device => device.Id == account.DeviceToRevoke)?.Current == true;
                await _auth.RevokeDeviceAsync(account.DeviceToRevoke);
                if (current)
                {
                    await _auth.LogoutAsync();
                    ShowAuthPanel();
                }
            }
            catch (Exception exception) { MessageBox.Show(this, exception.Message, "解绑失败"); }
        }
    }

    private void MainWindow_SourceInitialized(object? sender, EventArgs e)
    {
        _windowSource = HwndSource.FromHwnd(new WindowInteropHelper(this).Handle);
        _windowSource?.AddHook(WindowMessageHook);
    }

    private IntPtr WindowMessageHook(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        const int WmPowerBroadcast = 0x0218;
        const int ResumeAutomatic = 18;
        const int ResumeSuspend = 7;
        if (message == WmPowerBroadcast && (wParam.ToInt32() == ResumeAutomatic || wParam.ToInt32() == ResumeSuspend))
        {
            _ = Dispatcher.BeginInvoke(new Action(() => _ = RefreshAuthorizationAsync()));
        }
        return IntPtr.Zero;
    }

    private async void Backend_SnapshotChanged(object? sender, BackendSnapshot snapshot)
    {
        switch (snapshot.Status)
        {
            case BackendStatus.Idle:
            case BackendStatus.Starting:
                ShowPanel(LaunchPanel);
                break;
            case BackendStatus.Ready when snapshot.BaseUrl is not null:
                _currentBaseUrl = snapshot.BaseUrl;
                StartupDurationText.Text = snapshot.StartupDuration is null
                    ? string.Empty
                    : $"· {snapshot.StartupDuration.Value.TotalSeconds:0.0} 秒";
                ShowPanel(ReadyPanel);
                try
                {
                    await EnsureBrowserAndNavigateAsync(snapshot.BaseUrl);
                }
                catch (Exception exception)
                {
                    ShowFailure("内嵌浏览器初始化失败。", exception.ToString());
                }
                break;
            case BackendStatus.Failed:
                ShowFailure(snapshot.Message, snapshot.Details);
                break;
        }
    }

    private void ShowFailure(string message, string details)
    {
        FailureMessageText.Text = message;
        FailureDetailsText.Text = details;
        FailureDetailsExpander.Visibility = string.IsNullOrWhiteSpace(details)
            ? Visibility.Collapsed
            : Visibility.Visible;
        ShowPanel(FailurePanel);
    }

    private void ShowRuntimePanel()
    {
        var installerPath = GetWebViewInstallerPath();
        var installerAvailable = File.Exists(installerPath);
        InstallRuntimeButton.IsEnabled = installerAvailable;
        RuntimeMessageText.Text = installerAvailable
            ? "APP 已附带微软官方离线安装程序。确认后将按当前用户安装，完成后自动启动工具。"
            : $"未找到附带的离线安装程序：{installerPath}。请重新下载完整安装包。";
        ShowPanel(RuntimePanel);
    }

    private void ShowPanel(FrameworkElement visiblePanel)
    {
        LaunchPanel.Visibility = visiblePanel == LaunchPanel ? Visibility.Visible : Visibility.Collapsed;
        AuthPanel.Visibility = visiblePanel == AuthPanel ? Visibility.Visible : Visibility.Collapsed;
        ReadyPanel.Visibility = visiblePanel == ReadyPanel ? Visibility.Visible : Visibility.Collapsed;
        FailurePanel.Visibility = visiblePanel == FailurePanel ? Visibility.Visible : Visibility.Collapsed;
        RuntimePanel.Visibility = visiblePanel == RuntimePanel ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task EnsureBrowserAndNavigateAsync(Uri url)
    {
        if (!_browserInitialized)
        {
            var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var userDataFolder = Path.Combine(localAppData, "WuLab", "My Bio Tools", "WebView2");
            Directory.CreateDirectory(userDataFolder);

            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: userDataFolder);
            await Browser.EnsureCoreWebView2Async(environment);

            var core = Browser.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.IsZoomControlEnabled = true;
            core.Settings.IsStatusBarEnabled = false;
            core.NavigationStarting += Core_NavigationStarting;
            core.NewWindowRequested += Core_NewWindowRequested;
            core.DownloadStarting += Core_DownloadStarting;
            _browserInitialized = true;
        }

        Browser.CoreWebView2.Navigate(url.AbsoluteUri);
    }

    private void Core_NavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) || IsLocalAppUri(uri))
        {
            return;
        }

        args.Cancel = true;
        OpenExternalUri(uri);
    }

    private void Core_NewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        args.Handled = true;
        if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri))
        {
            return;
        }

        if (IsLocalAppUri(uri))
        {
            Browser.CoreWebView2.Navigate(uri.AbsoluteUri);
        }
        else
        {
            OpenExternalUri(uri);
        }
    }

    private void Core_DownloadStarting(object? sender, CoreWebView2DownloadStartingEventArgs args)
    {
        var suggestedName = Path.GetFileName(args.ResultFilePath);
        if (string.IsNullOrWhiteSpace(suggestedName))
        {
            suggestedName = "MyBioTools-结果";
        }

        var destination = GetUniqueDownloadPath(suggestedName);
        args.ResultFilePath = destination;
        args.Handled = true;

        var operation = args.DownloadOperation;
        operation.StateChanged += Operation_StateChanged;

        void Operation_StateChanged(object? operationSender, object eventArgs)
        {
            if (operation.State == CoreWebView2DownloadState.Completed)
            {
                operation.StateChanged -= Operation_StateChanged;
                Dispatcher.BeginInvoke(new Action(() => RevealInExplorer(destination)));
            }
            else if (operation.State == CoreWebView2DownloadState.Interrupted)
            {
                operation.StateChanged -= Operation_StateChanged;
            }
        }
    }

    private static string GetUniqueDownloadPath(string suggestedName)
    {
        var downloads = KnownFolders.GetDownloadsPath();
        Directory.CreateDirectory(downloads);

        var safeName = Path.GetFileName(suggestedName);
        var destination = Path.Combine(downloads, safeName);
        if (!File.Exists(destination))
        {
            return destination;
        }

        var extension = Path.GetExtension(safeName);
        var stem = Path.GetFileNameWithoutExtension(safeName);
        for (var index = 2; ; index++)
        {
            var candidate = Path.Combine(downloads, $"{stem} {index}{extension}");
            if (!File.Exists(candidate))
            {
                return candidate;
            }
        }
    }

    private bool IsLocalAppUri(Uri uri)
    {
        if (uri.Scheme is "about" or "blob" or "data")
        {
            return true;
        }

        if (_currentBaseUrl is null || uri.Scheme != Uri.UriSchemeHttp)
        {
            return false;
        }

        var loopbackHost = uri.Host.Equals("127.0.0.1", StringComparison.OrdinalIgnoreCase)
            || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase);
        return loopbackHost && uri.Port == _currentBaseUrl.Port;
    }

    private void OpenExternalUri(Uri uri)
    {
        if (uri.Scheme != Uri.UriSchemeHttp
            && uri.Scheme != Uri.UriSchemeHttps
            && uri.Scheme != "mailto")
        {
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
        catch (Exception exception)
        {
            MessageBox.Show(this, exception.Message, "无法打开外部链接", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private static void RevealInExplorer(string path)
    {
        Process.Start(new ProcessStartInfo("explorer.exe")
        {
            Arguments = $"/select,\"{path}\"",
            UseShellExecute = true,
        });
    }

    private void RefreshButton_Click(object sender, RoutedEventArgs e)
    {
        if (_browserInitialized && Browser.CoreWebView2 is not null)
        {
            Browser.Reload();
        }
    }

    private async void RestartButton_Click(object sender, RoutedEventArgs e)
    {
        ShowPanel(LaunchPanel);
        _currentBaseUrl = null;
        await _backend.RestartAsync();
    }

    private void OpenLogButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _backend.OpenLog();
        }
        catch (Exception exception)
        {
            MessageBox.Show(this, exception.Message, "无法打开运行日志", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private async void InstallRuntimeButton_Click(object sender, RoutedEventArgs e)
    {
        var installerPath = GetWebViewInstallerPath();
        if (!File.Exists(installerPath))
        {
            ShowRuntimePanel();
            return;
        }

        var answer = MessageBox.Show(
            this,
            "将运行微软官方 WebView2 Runtime 离线安装程序。是否继续？",
            "安装 WebView2 Runtime",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question);
        if (answer != MessageBoxResult.Yes)
        {
            return;
        }

        InstallRuntimeButton.IsEnabled = false;
        RuntimeMessageText.Text = "正在安装 WebView2 Runtime，请稍候…";
        try
        {
            using var process = Process.Start(new ProcessStartInfo(installerPath)
            {
                Arguments = "/silent /install",
                UseShellExecute = true,
            });
            if (process is null)
            {
                throw new InvalidOperationException("无法启动 WebView2 Runtime 安装程序。");
            }

            await process.WaitForExitAsync();
            if (process.ExitCode != 0 || !await WaitForWebViewRuntimeAsync())
            {
                throw new InvalidOperationException($"WebView2 Runtime 安装未完成（代码 {process.ExitCode}）。");
            }

            _backendStarted = false;
            await StartApplicationAsync();
        }
        catch (Exception exception)
        {
            RuntimeMessageText.Text = exception.Message;
            InstallRuntimeButton.IsEnabled = true;
        }
    }

    private static bool IsWebViewRuntimeAvailable()
    {
        try
        {
            var version = CoreWebView2Environment.GetAvailableBrowserVersionString();
            return !string.IsNullOrWhiteSpace(version) && version != "0.0.0.0";
        }
        catch (WebView2RuntimeNotFoundException)
        {
            return false;
        }
        catch
        {
            return false;
        }
    }

    private static async Task<bool> WaitForWebViewRuntimeAsync()
    {
        for (var attempt = 0; attempt < 30; attempt++)
        {
            if (IsWebViewRuntimeAvailable())
            {
                return true;
            }
            await Task.Delay(1_000);
        }
        return false;
    }

    private static string GetWebViewInstallerPath()
    {
        return Path.Combine(AppContext.BaseDirectory, "prerequisites", WebViewInstallerName);
    }

    private async void Window_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        var control = Keyboard.Modifiers.HasFlag(ModifierKeys.Control);
        var shift = Keyboard.Modifiers.HasFlag(ModifierKeys.Shift);
        if (!control)
        {
            return;
        }

        if (e.Key == Key.R && shift)
        {
            e.Handled = true;
            ShowPanel(LaunchPanel);
            _currentBaseUrl = null;
            await _backend.RestartAsync();
        }
        else if (e.Key == Key.R)
        {
            e.Handled = true;
            if (_browserInitialized && Browser.CoreWebView2 is not null)
            {
                Browser.Reload();
            }
        }
        else if (e.Key == Key.L && shift)
        {
            e.Handled = true;
            _backend.OpenLog();
        }
    }
}
