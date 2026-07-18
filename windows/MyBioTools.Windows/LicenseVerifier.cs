using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MyBioTools.Windows;

internal sealed class LicenseVerifier
{
    private static readonly BigInteger Q = (BigInteger.One << 255) - 19;
    private static readonly BigInteger L = (BigInteger.One << 252) + BigInteger.Parse("27742317777372353535851937790883648493");
    private static readonly BigInteger D = Mod(-121665 * BigInteger.ModPow(121666, Q - 2, Q));
    private static readonly BigInteger I = BigInteger.ModPow(2, (Q - 1) / 4, Q);
    private static readonly Point Identity = new(0, 1, 1, 0);
    private static readonly Point BasePoint = CreateBasePoint();

    private sealed record PublicJwk(string Kty, string Crv, string X);
    private sealed record Header(string Alg, string Typ);
    private readonly record struct Point(BigInteger X, BigInteger Y, BigInteger Z, BigInteger T);

    public string InstallationHash(string installationId)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes($"my-bio-tools-installation:{installationId}"));
        return Base64UrlEncode(digest);
    }

    public OfflineLicenseClaims Verify(
        string token,
        string publicJwkJson,
        string installationId,
        long? lastTrustedServerTime,
        DateTimeOffset? currentTime = null)
    {
        var parts = token.Split('.');
        if (parts.Length != 3) throw new InvalidDataException("离线授权格式无效。");
        var header = JsonSerializer.Deserialize<Header>(Base64UrlDecode(parts[0]), AuthJson.Options)
            ?? throw new InvalidDataException("离线授权头无效。");
        var claims = JsonSerializer.Deserialize<OfflineLicenseClaims>(Base64UrlDecode(parts[1]), AuthJson.Options)
            ?? throw new InvalidDataException("离线授权内容无效。");
        var jwk = JsonSerializer.Deserialize<PublicJwk>(publicJwkJson, AuthJson.Options)
            ?? throw new InvalidDataException("授权公钥无效。");
        if (header.Alg != "EdDSA" || header.Typ != "JWT" || jwk.Kty != "OKP" || jwk.Crv != "Ed25519")
            throw new InvalidDataException("离线授权算法无效。");
        var message = Encoding.ASCII.GetBytes($"{parts[0]}.{parts[1]}");
        if (!VerifyEd25519(Base64UrlDecode(jwk.X), message, Base64UrlDecode(parts[2])))
            throw new InvalidDataException("离线授权签名无效。");
        var now = (currentTime ?? DateTimeOffset.UtcNow).ToUnixTimeSeconds();
        if (claims.Type != "offline-license" || claims.Version != 1)
            throw new InvalidDataException("离线授权类型无效。");
        if (claims.Device != InstallationHash(installationId))
            throw new InvalidDataException("授权不属于当前设备。");
        if (claims.ExpiresAt <= now) throw new InvalidDataException("离线授权已过期，请联网验证。");
        if (claims.IssuedAt > now + 300) throw new InvalidDataException("离线授权签发时间无效。");
        if (lastTrustedServerTime is not null && now + 300 < lastTrustedServerTime)
            throw new InvalidDataException("检测到系统时间回拨，需要联网验证。");
        return claims;
    }

    internal static bool VerifyEd25519(byte[] publicKey, byte[] message, byte[] signature)
    {
        try
        {
            if (publicKey.Length != 32 || signature.Length != 64) return false;
            var publicPoint = DecodePoint(publicKey);
            var rPoint = DecodePoint(signature[..32]);
            var scalar = FromLittleEndian(signature[32..]);
            if (scalar >= L) return false;
            if (!PointsEqual(ScalarMultiply(publicPoint, L), Identity)) return false;
            if (!PointsEqual(ScalarMultiply(rPoint, L), Identity)) return false;
            var challengeInput = new byte[64 + message.Length];
            signature.AsSpan(0, 32).CopyTo(challengeInput);
            publicKey.CopyTo(challengeInput, 32);
            message.CopyTo(challengeInput, 64);
            var challenge = FromLittleEndian(SHA512.HashData(challengeInput)) % L;
            return PointsEqual(
                ScalarMultiply(BasePoint, scalar),
                Add(rPoint, ScalarMultiply(publicPoint, challenge)));
        }
        catch (InvalidDataException)
        {
            return false;
        }
    }

    private static Point CreateBasePoint()
    {
        var y = Mod(4 * BigInteger.ModPow(5, Q - 2, Q));
        var x = RecoverX(y);
        if (!x.IsEven) x = Q - x;
        return new Point(x, y, 1, Mod(x * y));
    }

    private static BigInteger RecoverX(BigInteger y)
    {
        var xx = Mod((y * y - 1) * BigInteger.ModPow(Mod(D * y * y + 1), Q - 2, Q));
        var x = BigInteger.ModPow(xx, (Q + 3) / 8, Q);
        if (Mod(x * x - xx) != 0) x = Mod(x * I);
        if (Mod(x * x - xx) != 0) throw new InvalidDataException("Ed25519 点不在曲线上。");
        return x;
    }

    private static Point DecodePoint(byte[] encoded)
    {
        if (encoded.Length != 32) throw new InvalidDataException("Ed25519 点长度无效。");
        var value = FromLittleEndian(encoded);
        var sign = (int)(value >> 255);
        var y = value & ((BigInteger.One << 255) - 1);
        if (y >= Q) throw new InvalidDataException("Ed25519 点编码不规范。");
        var x = RecoverX(y);
        if ((!x.IsEven ? 1 : 0) != sign) x = Q - x;
        var point = new Point(x, y, 1, Mod(x * y));
        if (!EncodePoint(point).SequenceEqual(encoded)) throw new InvalidDataException("Ed25519 点编码不规范。");
        return point;
    }

    private static byte[] EncodePoint(Point point)
    {
        var inverse = BigInteger.ModPow(point.Z, Q - 2, Q);
        var x = Mod(point.X * inverse);
        var y = Mod(point.Y * inverse);
        var value = y | ((!x.IsEven ? BigInteger.One : BigInteger.Zero) << 255);
        var bytes = value.ToByteArray(isUnsigned: true, isBigEndian: false);
        Array.Resize(ref bytes, 32);
        return bytes;
    }

    private static Point Add(Point first, Point second)
    {
        var a = Mod((first.Y - first.X) * (second.Y - second.X));
        var b = Mod((first.Y + first.X) * (second.Y + second.X));
        var c = Mod(2 * D * first.T * second.T);
        var d = Mod(2 * first.Z * second.Z);
        var e = Mod(b - a); var f = Mod(d - c); var g = Mod(d + c); var h = Mod(b + a);
        return new Point(Mod(e * f), Mod(g * h), Mod(f * g), Mod(e * h));
    }

    private static Point Double(Point point)
    {
        var a = Mod(point.X * point.X); var b = Mod(point.Y * point.Y);
        var c = Mod(2 * point.Z * point.Z); var d = Mod(-a);
        var e = Mod((point.X + point.Y) * (point.X + point.Y) - a - b);
        var g = Mod(d + b); var f = Mod(g - c); var h = Mod(d - b);
        return new Point(Mod(e * f), Mod(g * h), Mod(f * g), Mod(e * h));
    }

    private static Point ScalarMultiply(Point point, BigInteger scalar)
    {
        var result = Identity; var current = point;
        while (scalar > 0)
        {
            if (!scalar.IsEven) result = Add(result, current);
            current = Double(current); scalar >>= 1;
        }
        return result;
    }

    private static bool PointsEqual(Point first, Point second) =>
        Mod(first.X * second.Z - second.X * first.Z) == 0
        && Mod(first.Y * second.Z - second.Y * first.Z) == 0;

    private static BigInteger Mod(BigInteger value)
    {
        value %= Q;
        return value.Sign < 0 ? value + Q : value;
    }

    private static BigInteger FromLittleEndian(byte[] bytes) => new(bytes, isUnsigned: true, isBigEndian: false);
    private static byte[] Base64UrlDecode(string value) => Convert.FromBase64String(
        value.Replace('-', '+').Replace('_', '/') + new string('=', (4 - value.Length % 4) % 4));
    private static string Base64UrlEncode(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}
