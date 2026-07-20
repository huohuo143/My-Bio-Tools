using System.Text.Json.Serialization;

namespace MyBioTools.Windows;

internal sealed record UserProfile(
    string Id,
    string Email,
    string RealName,
    string LabRole,
    string Status,
    string? ReviewReason,
    long? AuthorizationExpiresAt,
    bool? AuthorizationPermanent);

internal sealed record DeviceProfile(
    string Id,
    string Platform,
    string DeviceName,
    string AppVersion,
    long FirstSeenAt,
    long LastSeenAt,
    long? RevokedAt,
    bool Current);

internal sealed record AuthTokens(
    string AccessToken,
    long AccessExpiresAt,
    string RefreshToken,
    long RefreshExpiresAt,
    string OfflineLicense,
    long OfflineLicenseExpiresAt,
    long ServerTime);

internal sealed record StoredSession(UserProfile User, AuthTokens Tokens);

internal sealed record StoredAuthState(
    string InstallationId,
    StoredSession? Session,
    long? LastTrustedServerTime);

internal sealed record BackendAuthorization(
    string OfflineLicense,
    string InstallationHash,
    string PublicJwk,
    string OmicsKeyB64);

internal sealed record AuthConfiguration(string BaseUrl, string PublicJwk)
{
    public static AuthConfiguration Load()
    {
        var overrideUrl = Environment.GetEnvironmentVariable("MY_BIO_TOOLS_AUTH_BASE_URL");
        var overrideKey = Environment.GetEnvironmentVariable("MY_BIO_TOOLS_LICENSE_PUBLIC_JWK");
        if (!string.IsNullOrWhiteSpace(overrideUrl) && !string.IsNullOrWhiteSpace(overrideKey))
        {
            return Validate(new AuthConfiguration(overrideUrl, overrideKey));
        }

        var path = Path.Combine(AppContext.BaseDirectory, "auth-config.json");
        if (!File.Exists(path))
        {
            throw new AuthConfigurationException("安装包缺少 auth-config.json 授权配置。");
        }
        var configuration = System.Text.Json.JsonSerializer.Deserialize<AuthConfiguration>(
            File.ReadAllText(path), AuthJson.Options)
            ?? throw new AuthConfigurationException("授权配置格式无效。");
        return Validate(configuration);
    }

    private static AuthConfiguration Validate(AuthConfiguration configuration)
    {
        if (!Uri.TryCreate(configuration.BaseUrl, UriKind.Absolute, out var uri)
            || uri.Scheme != Uri.UriSchemeHttps
            || string.IsNullOrWhiteSpace(configuration.PublicJwk))
        {
            throw new AuthConfigurationException("授权服务地址或生产公钥尚未配置。");
        }
        return configuration with { BaseUrl = configuration.BaseUrl.TrimEnd('/') };
    }
}

internal sealed class AuthConfigurationException(string message) : Exception(message);

internal sealed class AuthApiException(string code, string message, int statusCode) : Exception(message)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
    public bool IsExplicitRevocation => statusCode is 401 or 403
        || Code is "AUTHORIZATION_REVOKED" or "AUTHORIZATION_EXPIRED" or "ACCOUNT_SUSPENDED" or "ACCOUNT_DELETED" or "SESSION_EXPIRED";
}

internal sealed record OfflineLicenseClaims(
    [property: JsonPropertyName("typ")] string Type,
    [property: JsonPropertyName("sub")] string Subject,
    [property: JsonPropertyName("device")] string Device,
    [property: JsonPropertyName("iat")] long IssuedAt,
    [property: JsonPropertyName("exp")] long ExpiresAt,
    [property: JsonPropertyName("version")] int Version,
    [property: JsonPropertyName("omics_key_b64")] string OmicsKeyB64);

internal static class AuthJson
{
    public static readonly System.Text.Json.JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase,
    };
}
