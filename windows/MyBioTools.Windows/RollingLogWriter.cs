using System.Text;

namespace MyBioTools.Windows;

internal sealed class RollingLogWriter : IDisposable
{
    private const long MaximumBytes = 5L * 1024 * 1024;
    private const int BackupCount = 3;
    private readonly object _gate = new();

    public RollingLogWriter()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        LogPath = Path.Combine(localAppData, "WuLab", "My Bio Tools", "Logs", "backend.log");
    }

    public string LogPath { get; }

    public void EnsureExists()
    {
        lock (_gate)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            if (!File.Exists(LogPath))
            {
                using var _ = File.Create(LogPath);
            }
        }
    }

    public void Append(string text)
    {
        lock (_gate)
        {
            try
            {
                EnsureExistsWithoutLock();
                RotateIfNeeded(Encoding.UTF8.GetByteCount(text));
                File.AppendAllText(LogPath, text, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            }
            catch
            {
                // Logging must never interrupt scientific work or app startup.
            }
        }
    }

    private void EnsureExistsWithoutLock()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
        if (!File.Exists(LogPath))
        {
            using var _ = File.Create(LogPath);
        }
    }

    private void RotateIfNeeded(int incomingBytes)
    {
        var currentLength = new FileInfo(LogPath).Length;
        if (currentLength + incomingBytes <= MaximumBytes)
        {
            return;
        }

        var oldest = $"{LogPath}.{BackupCount}";
        if (File.Exists(oldest))
        {
            File.Delete(oldest);
        }

        for (var index = BackupCount - 1; index >= 1; index--)
        {
            var source = $"{LogPath}.{index}";
            var destination = $"{LogPath}.{index + 1}";
            if (File.Exists(source))
            {
                File.Move(source, destination, overwrite: true);
            }
        }

        File.Move(LogPath, $"{LogPath}.1", overwrite: true);
        using var _ = File.Create(LogPath);
    }

    public void Dispose()
    {
    }
}
