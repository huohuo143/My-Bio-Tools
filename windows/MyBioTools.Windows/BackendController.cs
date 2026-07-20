using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Windows.Threading;

namespace MyBioTools.Windows;

internal enum BackendStatus
{
    Idle,
    Starting,
    Ready,
    Failed,
}

internal sealed record BackendSnapshot(
    BackendStatus Status,
    Uri? BaseUrl = null,
    string Message = "",
    string Details = "",
    TimeSpan? StartupDuration = null);

internal sealed class BackendController : IDisposable
{
    private const int RecentOutputLimit = 20_000;
    private static readonly TimeSpan StartupTimeout = TimeSpan.FromSeconds(60);

    private readonly Dispatcher _dispatcher;
    private readonly SemaphoreSlim _lifecycleGate = new(1, 1);
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(1) };
    private readonly RollingLogWriter _logWriter = new();
    private readonly object _outputLock = new();
    private readonly StringBuilder _recentOutput = new();

    private Process? _process;
    private JobObject? _jobObject;
    private CancellationTokenSource? _healthCancellation;
    private Guid _launchId = Guid.Empty;
    private bool _stopping;
    private bool _disposed;
    private BackendAuthorization? _authorization;
    private OmicsUnlockResult? _omicsUnlock;

    public BackendController(Dispatcher dispatcher)
    {
        _dispatcher = dispatcher;
    }

    public event EventHandler<BackendSnapshot>? SnapshotChanged;

    public string LogPath => _logWriter.LogPath;

    public void ConfigureAuthorization(BackendAuthorization? authorization)
    {
        _authorization = authorization;
        if (authorization is null)
        {
            StopCore();
            Publish(new BackendSnapshot(BackendStatus.Idle));
        }
    }

    public async Task StartAsync()
    {
        await _lifecycleGate.WaitAsync();
        try
        {
            ThrowIfDisposed();
            StopCore();
            await StartCoreAsync();
        }
        finally
        {
            _lifecycleGate.Release();
        }
    }

    public async Task RestartAsync()
    {
        await _lifecycleGate.WaitAsync();
        try
        {
            ThrowIfDisposed();
            StopCore();
            await Task.Delay(300);
            await StartCoreAsync();
        }
        finally
        {
            _lifecycleGate.Release();
        }
    }

    public void OpenLog()
    {
        _logWriter.EnsureExists();
        Process.Start(new ProcessStartInfo(LogPath) { UseShellExecute = true });
    }

    private async Task StartCoreAsync()
    {
        if (_authorization is null)
        {
            Publish(new BackendSnapshot(BackendStatus.Idle));
            return;
        }
        ClearRecentOutput();
        Publish(new BackendSnapshot(BackendStatus.Starting));

        var launchId = Guid.NewGuid();
        _launchId = launchId;
        _stopping = false;
        var beganAt = Stopwatch.StartNew();

        try
        {
            var resources = ResolveResources();
            _omicsUnlock = OmicsDatabaseUnlocker.Unlock(
                resources.AppSource,
                _authorization.OmicsKeyB64);
            var port = GetAvailableLoopbackPort();
            var baseUrl = new Uri($"http://127.0.0.1:{port}/");

            var startInfo = new ProcessStartInfo
            {
                FileName = resources.Backend,
                WorkingDirectory = resources.AppSource,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            startInfo.ArgumentList.Add("--port");
            startInfo.ArgumentList.Add(port.ToString());
            startInfo.ArgumentList.Add("--app-dir");
            startInfo.ArgumentList.Add(resources.AppSource);
            startInfo.Environment["PYTHONNOUSERSITE"] = "1";
            startInfo.Environment["ARROW_DEFAULT_MEMORY_POOL"] = "system";
            startInfo.Environment["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false";
            startInfo.Environment["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none";
            startInfo.Environment["MY_BIO_TOOLS_ACCESS_MODE"] = "authorized";
            startInfo.Environment["MY_BIO_TOOLS_OFFLINE_LICENSE"] = _authorization.OfflineLicense;
            startInfo.Environment["MY_BIO_TOOLS_INSTALLATION_HASH"] = _authorization.InstallationHash;
            startInfo.Environment["MY_BIO_TOOLS_LICENSE_PUBLIC_JWK"] = _authorization.PublicJwk;
            startInfo.Environment["MY_BIO_TOOLS_OMICS_KEY_B64"] = _authorization.OmicsKeyB64;
            startInfo.Environment["MY_BIO_TOOLS_OMICS_UNLOCK_DIR"] = _omicsUnlock.DirectoryPath;
            startInfo.Environment["MY_BIO_TOOLS_OMICS_DB"] = _omicsUnlock.DatabasePath;

            var process = new Process
            {
                StartInfo = startInfo,
                EnableRaisingEvents = true,
            };
            process.OutputDataReceived += (_, args) => RecordLine(args.Data);
            process.ErrorDataReceived += (_, args) => RecordLine(args.Data);
            process.Exited += (_, _) => HandleUnexpectedExit(process, launchId);

            if (!process.Start())
            {
                throw new InvalidOperationException("无法启动内置 Python 服务。");
            }

            var jobObject = new JobObject();
            jobObject.Assign(process);
            _jobObject = jobObject;
            _process = process;
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            _healthCancellation = new CancellationTokenSource(StartupTimeout);
            var ready = await WaitForHealthAsync(baseUrl, launchId, _healthCancellation.Token);
            if (!ready || _launchId != launchId || _stopping)
            {
                return;
            }

            beganAt.Stop();
            Publish(new BackendSnapshot(
                BackendStatus.Ready,
                BaseUrl: baseUrl,
                StartupDuration: beganAt.Elapsed));
        }
        catch (OperationCanceledException) when (_launchId == launchId && !_stopping)
        {
            if (_process is not null && _process.HasExited)
            {
                return;
            }
            TerminateCurrentProcess();
            Publish(new BackendSnapshot(
                BackendStatus.Failed,
                Message: "内置服务在 60 秒内未能启动。请重试或打开运行日志。",
                Details: GetRecentOutput()));
        }
        catch (Exception exception)
        {
            RecordLine($"启动失败：{exception}");
            TerminateCurrentProcess();
            Publish(new BackendSnapshot(
                BackendStatus.Failed,
                Message: exception.Message,
                Details: GetRecentOutput()));
        }
    }

    private async Task<bool> WaitForHealthAsync(Uri baseUrl, Guid launchId, CancellationToken cancellationToken)
    {
        var healthUrl = new Uri(baseUrl, "_stcore/health");
        while (!cancellationToken.IsCancellationRequested && _launchId == launchId && !_stopping)
        {
            try
            {
                using var response = await _httpClient.GetAsync(healthUrl, cancellationToken);
                if (response.StatusCode == HttpStatusCode.OK)
                {
                    return true;
                }
            }
            catch (HttpRequestException)
            {
                // The bundled service normally needs several seconds to become ready.
            }
            catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                // One health request timed out; continue until the global timeout.
            }

            await Task.Delay(250, cancellationToken);
        }

        cancellationToken.ThrowIfCancellationRequested();
        return false;
    }

    private void HandleUnexpectedExit(Process process, Guid launchId)
    {
        if (_disposed || _stopping || _launchId != launchId)
        {
            return;
        }

        int exitCode;
        try
        {
            exitCode = process.ExitCode;
        }
        catch
        {
            exitCode = -1;
        }

        _healthCancellation?.Cancel();
        CleanupOmicsUnlock();
        Publish(new BackendSnapshot(
            BackendStatus.Failed,
            Message: $"内置服务意外退出（代码 {exitCode}）。请打开运行日志查看详情。",
            Details: GetRecentOutput()));
    }

    private static (string Backend, string AppSource) ResolveResources()
    {
        var root = Environment.GetEnvironmentVariable("MY_BIO_TOOLS_RESOURCE_ROOT");
        if (string.IsNullOrWhiteSpace(root))
        {
            root = AppContext.BaseDirectory;
        }

        var backend = Path.GetFullPath(Path.Combine(root, "backend", "BioToolsBackend.exe"));
        var appSource = Path.GetFullPath(Path.Combine(root, "app_source"));
        var mainScript = Path.Combine(appSource, "main.py");

        if (!File.Exists(backend))
        {
            throw new FileNotFoundException("内置运行环境缺失，请重新安装应用。", backend);
        }
        if (!File.Exists(mainScript))
        {
            throw new FileNotFoundException("工具入口文件缺失，请重新安装应用。", mainScript);
        }

        return (backend, appSource);
    }

    private static int GetAvailableLoopbackPort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            return ((IPEndPoint)listener.LocalEndpoint).Port;
        }
        finally
        {
            listener.Stop();
        }
    }

    private void StopCore()
    {
        _stopping = true;
        _launchId = Guid.NewGuid();
        _healthCancellation?.Cancel();
        _healthCancellation?.Dispose();
        _healthCancellation = null;
        TerminateCurrentProcess();
        _stopping = false;
    }

    private void TerminateCurrentProcess()
    {
        var process = _process;
        _process = null;
        if (process is not null)
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(3_000);
                }
            }
            catch
            {
                // Closing the Job Object below is the final process-tree safeguard.
            }
            finally
            {
                process.Dispose();
            }
        }

        _jobObject?.Dispose();
        _jobObject = null;
        CleanupOmicsUnlock();
    }

    private void CleanupOmicsUnlock()
    {
        var current = Interlocked.Exchange(ref _omicsUnlock, null);
        OmicsDatabaseUnlocker.Cleanup(current);
    }

    private void RecordLine(string? line)
    {
        if (string.IsNullOrEmpty(line))
        {
            return;
        }

        var text = line + Environment.NewLine;
        lock (_outputLock)
        {
            _recentOutput.Append(text);
            if (_recentOutput.Length > RecentOutputLimit)
            {
                _recentOutput.Remove(0, _recentOutput.Length - RecentOutputLimit);
            }
        }
        _logWriter.Append(text);
    }

    private string GetRecentOutput()
    {
        lock (_outputLock)
        {
            return _recentOutput.ToString();
        }
    }

    private void ClearRecentOutput()
    {
        lock (_outputLock)
        {
            _recentOutput.Clear();
        }
    }

    private void Publish(BackendSnapshot snapshot)
    {
        if (_disposed)
        {
            return;
        }

        _dispatcher.BeginInvoke(new Action(() =>
        {
            if (!_disposed)
            {
                SnapshotChanged?.Invoke(this, snapshot);
            }
        }));
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        StopCore();
        _httpClient.Dispose();
        // A startup task can still be unwinding on window close; leave the
        // semaphore for that task to release safely.
        _logWriter.Dispose();
    }
}
