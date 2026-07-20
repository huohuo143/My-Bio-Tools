using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace MyBioTools.Windows;

internal sealed record OmicsUnlockResult(string DirectoryPath, string DatabasePath);

internal static class OmicsDatabaseUnlocker
{
    private static readonly byte[] Magic = "MBTO2"u8.ToArray();
    private static readonly byte[] EncryptionLabel = Encoding.UTF8.GetBytes("My Bio Tools omics encryption");
    private static readonly byte[] AuthenticationLabel = Encoding.UTF8.GetBytes("My Bio Tools omics authentication");
    private const int HeaderSize = 5 + 8 + 16;
    private const int TagSize = 32;

    public static OmicsUnlockResult Unlock(string appSource, string masterKeyB64)
    {
        var encryptedPath = Path.Combine(
            appSource, "data", "lab_omics", "wulab_omics_v1.sqlite.zlib.aesctr");
        if (!File.Exists(encryptedPath))
        {
            throw new FileNotFoundException("安装包缺少加密多组学数据库。", encryptedPath);
        }

        byte[] masterKey;
        try
        {
            masterKey = Convert.FromBase64String(masterKeyB64);
        }
        catch (FormatException exception)
        {
            throw new InvalidDataException("授权中的多组学解锁信息无效。", exception);
        }
        if (masterKey.Length != 32)
        {
            CryptographicOperations.ZeroMemory(masterKey);
            throw new InvalidDataException("授权中的多组学解锁信息长度无效。");
        }

        var cacheRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "WuLab", "My Bio Tools", "Cache", "authenticated-omics");
        var unlockDirectory = Path.Combine(cacheRoot, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(unlockDirectory);
        var compressedPath = Path.Combine(unlockDirectory, "omics.sqlite.zlib");
        var databasePath = Path.Combine(unlockDirectory, "wulab_omics_v1.sqlite");

        byte[]? encryptionKey = null;
        byte[]? authenticationKey = null;
        try
        {
            encryptionKey = HMACSHA256.HashData(masterKey, EncryptionLabel);
            authenticationKey = HMACSHA256.HashData(masterKey, AuthenticationLabel);
            var (originalSize, initializationVector, ciphertextSize) = VerifyPackage(
                encryptedPath, authenticationKey);

            using (var input = File.OpenRead(encryptedPath))
            using (var output = new FileStream(compressedPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                input.Position = HeaderSize;
                TransformCtr(input, output, ciphertextSize, encryptionKey, initializationVector);
            }

            using (var compressed = File.OpenRead(compressedPath))
            using (var decompressor = new ZLibStream(compressed, CompressionMode.Decompress, leaveOpen: false))
            using (var database = new FileStream(databasePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                decompressor.CopyTo(database, 4 * 1024 * 1024);
            }

            File.Delete(compressedPath);
            if ((ulong)new FileInfo(databasePath).Length != originalSize)
            {
                throw new InvalidDataException("解锁后的多组学数据库大小与发布清单不一致。");
            }
            File.SetAttributes(databasePath, File.GetAttributes(databasePath) | FileAttributes.ReadOnly);
            return new OmicsUnlockResult(unlockDirectory, databasePath);
        }
        catch
        {
            CleanupDirectory(unlockDirectory);
            throw;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(masterKey);
            if (encryptionKey is not null) CryptographicOperations.ZeroMemory(encryptionKey);
            if (authenticationKey is not null) CryptographicOperations.ZeroMemory(authenticationKey);
        }
    }

    public static void Cleanup(OmicsUnlockResult? result)
    {
        if (result is not null) CleanupDirectory(result.DirectoryPath);
    }

    public static void ValidateRuntime()
    {
        // NIST SP 800-38A F.5 AES-256-CTR known-answer test. This also fixes
        // the counter byte order to the same big-endian convention as OpenSSL.
        var key = Convert.FromHexString(
            "603DEB1015CA71BE2B73AEF0857D7781" +
            "1F352C073B6108D72D9810A30914DFF4");
        var counter = Convert.FromHexString("F0F1F2F3F4F5F6F7F8F9FAFBFCFDFEFF");
        var ciphertext = Convert.FromHexString(
            "601EC313775789A5B7A7F504BBF3D228" +
            "F443E3CA4D62B59ACA84E990CACAF5C5" +
            "2B0930DAA23DE94CE87017BA2D84988D" +
            "DFC9C58DB67AADA613C2DD08457941A6");
        var expected = Convert.FromHexString(
            "6BC1BEE22E409F96E93D7E117393172A" +
            "AE2D8A571E03AC9C9EB76FAC45AF8E51" +
            "30C81C46A35CE411E5FBC1191A0A52EF" +
            "F69F2445DF4F9B17AD2B417BE66C3710");
        using var input = new MemoryStream(ciphertext, writable: false);
        using var output = new MemoryStream();
        TransformCtr(input, output, ciphertext.Length, key, counter);
        if (!CryptographicOperations.FixedTimeEquals(output.ToArray(), expected))
        {
            throw new CryptographicException("AES-256-CTR runtime self-test failed.");
        }
        CryptographicOperations.ZeroMemory(key);
    }

    private static (ulong OriginalSize, byte[] InitializationVector, long CiphertextSize) VerifyPackage(
        string encryptedPath,
        byte[] authenticationKey)
    {
        using var input = File.OpenRead(encryptedPath);
        if (input.Length <= HeaderSize + TagSize)
        {
            throw new InvalidDataException("加密多组学数据库不完整。");
        }
        var header = new byte[HeaderSize];
        input.ReadExactly(header);
        if (!header.AsSpan(0, Magic.Length).SequenceEqual(Magic))
        {
            throw new InvalidDataException("加密多组学数据库格式无效。");
        }
        var originalSize = BinaryPrimitives.ReadUInt64BigEndian(header.AsSpan(Magic.Length, 8));
        var initializationVector = header.AsSpan(Magic.Length + 8, 16).ToArray();
        var ciphertextSize = input.Length - HeaderSize - TagSize;

        using var authenticator = IncrementalHash.CreateHMAC(HashAlgorithmName.SHA256, authenticationKey);
        authenticator.AppendData(header);
        var buffer = new byte[4 * 1024 * 1024];
        var remaining = ciphertextSize;
        while (remaining > 0)
        {
            var read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
            if (read <= 0) throw new EndOfStreamException("加密多组学数据库提前结束。");
            authenticator.AppendData(buffer, 0, read);
            remaining -= read;
        }
        var expectedTag = new byte[TagSize];
        input.ReadExactly(expectedTag);
        var actualTag = authenticator.GetHashAndReset();
        if (!CryptographicOperations.FixedTimeEquals(actualTag, expectedTag))
        {
            throw new CryptographicException("加密多组学数据库完整性验证失败。");
        }
        return (originalSize, initializationVector, ciphertextSize);
    }

    private static void TransformCtr(
        Stream input,
        Stream output,
        long byteCount,
        byte[] key,
        byte[] initialCounter)
    {
        using var aes = Aes.Create();
        aes.KeySize = 256;
        aes.Mode = CipherMode.ECB;
        aes.Padding = PaddingMode.None;
        aes.Key = key;
        using var encryptor = aes.CreateEncryptor();
        var counter = initialCounter.ToArray();
        var keyStream = new byte[16];
        var block = new byte[16];
        var remaining = byteCount;
        while (remaining > 0)
        {
            var blockLength = (int)Math.Min(block.Length, remaining);
            input.ReadExactly(block.AsSpan(0, blockLength));
            if (encryptor.TransformBlock(counter, 0, counter.Length, keyStream, 0) != keyStream.Length)
            {
                throw new CryptographicException("AES-CTR 密钥流生成失败。");
            }
            for (var index = 0; index < blockLength; index++) block[index] ^= keyStream[index];
            output.Write(block, 0, blockLength);
            Array.Clear(block, 0, blockLength);
            IncrementCounter(counter);
            remaining -= blockLength;
        }
        CryptographicOperations.ZeroMemory(counter);
        CryptographicOperations.ZeroMemory(keyStream);
        CryptographicOperations.ZeroMemory(block);
    }

    private static void IncrementCounter(byte[] counter)
    {
        for (var index = counter.Length - 1; index >= 0; index--)
        {
            counter[index]++;
            if (counter[index] != 0) return;
        }
        throw new CryptographicException("AES-CTR counter overflow.");
    }

    private static void CleanupDirectory(string path)
    {
        try
        {
            if (!Directory.Exists(path)) return;
            foreach (var file in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories))
            {
                File.SetAttributes(file, FileAttributes.Normal);
            }
            Directory.Delete(path, recursive: true);
        }
        catch
        {
            // Best effort during shutdown; the next launch always uses a new directory.
        }
    }
}
