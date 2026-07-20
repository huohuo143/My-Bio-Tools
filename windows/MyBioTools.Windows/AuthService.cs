using System.Net.Http.Json;
using System.Text.Json;

namespace MyBioTools.Windows;

internal sealed class AuthService
{
    private sealed record MessageResponse(string Message);
    private sealed record SessionResponse(
        UserProfile User, string AccessToken, long AccessExpiresAt, string RefreshToken,
        long RefreshExpiresAt, string OfflineLicense, long OfflineLicenseExpiresAt, long ServerTime)
    {
        public StoredSession ToStored() => new(User, new AuthTokens(
            AccessToken, AccessExpiresAt, RefreshToken, RefreshExpiresAt,
            OfflineLicense, OfflineLicenseExpiresAt, ServerTime));
    }
    private sealed record DevicesResponse(DeviceProfile[] Devices);
    private sealed record ErrorEnvelope(ErrorDetail Error);
    private sealed record ErrorDetail(string Code, string Message);

    private readonly SecureCredentialStore _store = new();
    private readonly LicenseVerifier _verifier = new();
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(20) };
    private AuthConfiguration? _configuration;
    private StoredAuthState? _state;

    public UserProfile? User => _state?.Session?.User;
    public BackendAuthorization? Authorization { get; private set; }
    public bool IsOffline { get; private set; }
    public DateTimeOffset? LicenseExpiresAt { get; private set; }

    public async Task InitializeAsync()
    {
        _configuration = AuthConfiguration.Load();
        _state = _store.LoadOrCreate();
        if (_state.Session is null) return;
        await RefreshAsync();
    }

    public async Task LoginAsync(string email, string password)
    {
        EnsureConfigured();
        var response = await SendAsync<SessionResponse>(HttpMethod.Post, "/api/v1/login", new
        {
            email, password, installationId = _state!.InstallationId, platform = "windows",
            deviceName = Environment.MachineName,
            appVersion = typeof(AuthService).Assembly.GetName().Version?.ToString(3) ?? "1.9.7",
        });
        Accept(response.ToStored(), offline: false);
    }

    public async Task<string> RegisterAsync(
        string email, string realName, string labRole, string applicationNote, string password)
    {
        EnsureConfigured();
        var response = await SendAsync<MessageResponse>(HttpMethod.Post, "/api/v1/register", new
        {
            email, realName, labRole, applicationNote, password,
        });
        return response.Message;
    }

    public async Task<string> ForgotPasswordAsync(string email)
    {
        EnsureConfigured();
        return (await SendAsync<MessageResponse>(HttpMethod.Post, "/api/v1/password/forgot", new { email })).Message;
    }

    public async Task<string> ResendVerificationAsync(string email)
    {
        EnsureConfigured();
        return (await SendAsync<MessageResponse>(HttpMethod.Post, "/api/v1/email/resend", new { email })).Message;
    }

    public async Task RefreshAsync()
    {
        EnsureConfigured();
        var current = _state!.Session;
        if (current is null) return;
        try
        {
            var response = await SendAsync<SessionResponse>(HttpMethod.Post, "/api/v1/token/refresh", new
            {
                refreshToken = current.Tokens.RefreshToken,
                installationId = _state.InstallationId,
            });
            Accept(response.ToStored(), offline: false);
        }
        catch (AuthApiException exception) when (exception.IsExplicitRevocation)
        {
            Clear();
            throw;
        }
        catch (HttpRequestException)
        {
            Accept(current, offline: true);
        }
        catch (TaskCanceledException)
        {
            Accept(current, offline: true);
        }
    }

    public async Task<DeviceProfile[]> GetDevicesAsync()
    {
        await RefreshAsync();
        EnsureAuthorized();
        return (await SendAsync<DevicesResponse>(HttpMethod.Get, "/api/v1/me/devices", null, _state!.Session!.Tokens.AccessToken)).Devices;
    }

    public async Task RevokeDeviceAsync(string id)
    {
        await RefreshAsync();
        EnsureAuthorized();
        await SendAsync<MessageResponse>(HttpMethod.Delete, $"/api/v1/me/devices/{id}", null, _state!.Session!.Tokens.AccessToken);
    }

    public async Task LogoutAsync()
    {
        try { await RefreshAsync(); } catch { }
        if (_state?.Session is not null)
        {
            try { await SendAsync<MessageResponse>(HttpMethod.Post, "/api/v1/logout", new { }, _state.Session.Tokens.AccessToken); }
            catch { }
        }
        Clear();
    }

    private void Accept(StoredSession session, bool offline)
    {
        EnsureConfigured();
        var claims = _verifier.Verify(
            session.Tokens.OfflineLicense,
            _configuration!.PublicJwk,
            _state!.InstallationId,
            offline ? _state.LastTrustedServerTime : null);
        _state = _state with
        {
            Session = session,
            LastTrustedServerTime = offline ? _state.LastTrustedServerTime : session.Tokens.ServerTime,
        };
        _store.Save(_state);
        Authorization = new BackendAuthorization(
            session.Tokens.OfflineLicense,
            _verifier.InstallationHash(_state.InstallationId),
            _configuration.PublicJwk,
            claims.OmicsKeyB64);
        IsOffline = offline;
        LicenseExpiresAt = DateTimeOffset.FromUnixTimeSeconds(claims.ExpiresAt);
    }

    private void Clear()
    {
        if (_state is null) return;
        _state = _state with { Session = null };
        _store.Save(_state);
        Authorization = null;
        IsOffline = false;
        LicenseExpiresAt = null;
    }

    private void EnsureConfigured()
    {
        if (_configuration is null || _state is null) throw new InvalidOperationException("授权服务尚未初始化。");
    }

    private void EnsureAuthorized()
    {
        EnsureConfigured();
        if (Authorization is null || _state!.Session is null) throw new InvalidOperationException("账号尚未获得授权。");
    }

    private async Task<T> SendAsync<T>(HttpMethod method, string path, object? body, string? bearer = null)
    {
        using var request = new HttpRequestMessage(method, _configuration!.BaseUrl + path);
        request.Headers.Accept.ParseAdd("application/json");
        if (body is not null) request.Content = JsonContent.Create(body, options: AuthJson.Options);
        if (bearer is not null) request.Headers.Authorization = new("Bearer", bearer);
        using var response = await _httpClient.SendAsync(request);
        var data = await response.Content.ReadAsByteArrayAsync();
        if (!response.IsSuccessStatusCode)
        {
            var envelope = JsonSerializer.Deserialize<ErrorEnvelope>(data, AuthJson.Options);
            throw new AuthApiException(
                envelope?.Error.Code ?? $"HTTP_{(int)response.StatusCode}",
                envelope?.Error.Message ?? "授权请求失败。",
                (int)response.StatusCode);
        }
        return JsonSerializer.Deserialize<T>(data, AuthJson.Options)
            ?? throw new InvalidDataException("授权服务返回无效数据。");
    }
}
