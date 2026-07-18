using System.Runtime.InteropServices;

namespace MyBioTools.Windows;

internal static class KnownFolders
{
    private static readonly Guid DownloadsFolderId = new("374DE290-123F-4565-9164-39C4925E467B");

    public static string GetDownloadsPath()
    {
        var result = NativeMethods.SHGetKnownFolderPath(
            DownloadsFolderId,
            flags: 0,
            token: IntPtr.Zero,
            out var pathPointer);
        if (result == 0)
        {
            try
            {
                var path = Marshal.PtrToStringUni(pathPointer);
                if (!string.IsNullOrWhiteSpace(path))
                {
                    return path;
                }
            }
            finally
            {
                Marshal.FreeCoTaskMem(pathPointer);
            }
        }

        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "Downloads");
    }

    private static class NativeMethods
    {
        [DllImport("shell32.dll")]
        public static extern int SHGetKnownFolderPath(
            [MarshalAs(UnmanagedType.LPStruct)] Guid folderId,
            uint flags,
            IntPtr token,
            out IntPtr path);
    }
}
