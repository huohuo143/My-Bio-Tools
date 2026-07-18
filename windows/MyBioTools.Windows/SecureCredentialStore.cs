using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace MyBioTools.Windows;

internal sealed class SecureCredentialStore
{
    private const int CryptProtectUiForbidden = 0x1;
    private readonly string _path;

    public SecureCredentialStore()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        _path = Path.Combine(localAppData, "WuLab", "My Bio Tools", "auth.dat");
    }

    public StoredAuthState LoadOrCreate()
    {
        if (!File.Exists(_path))
        {
            var created = new StoredAuthState(Guid.NewGuid().ToString("D"), null, null);
            Save(created);
            return created;
        }
        var clear = Unprotect(File.ReadAllBytes(_path));
        try
        {
            return JsonSerializer.Deserialize<StoredAuthState>(clear, AuthJson.Options)
                ?? throw new InvalidDataException("授权凭据为空。");
        }
        finally
        {
            System.Security.Cryptography.CryptographicOperations.ZeroMemory(clear);
        }
    }

    public void Save(StoredAuthState state)
    {
        var clear = JsonSerializer.SerializeToUtf8Bytes(state, AuthJson.Options);
        try
        {
            var encrypted = Protect(clear);
            Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
            var temporary = _path + ".tmp";
            File.WriteAllBytes(temporary, encrypted);
            File.Move(temporary, _path, true);
        }
        finally
        {
            System.Security.Cryptography.CryptographicOperations.ZeroMemory(clear);
        }
    }

    private static byte[] Protect(byte[] input) => Transform(input, protect: true);
    private static byte[] Unprotect(byte[] input) => Transform(input, protect: false);

    private static byte[] Transform(byte[] input, bool protect)
    {
        var inputBlob = DataBlob.FromBytes(input);
        try
        {
            DataBlob outputBlob;
            var success = protect
                ? CryptProtectData(ref inputBlob, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, CryptProtectUiForbidden, out outputBlob)
                : CryptUnprotectData(ref inputBlob, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, CryptProtectUiForbidden, out outputBlob);
            if (!success) throw new Win32Exception(Marshal.GetLastWin32Error());
            try
            {
                var output = new byte[outputBlob.Size];
                Marshal.Copy(outputBlob.Data, output, 0, output.Length);
                return output;
            }
            finally
            {
                if (outputBlob.Data != IntPtr.Zero) LocalFree(outputBlob.Data);
            }
        }
        finally
        {
            inputBlob.Dispose();
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob : IDisposable
    {
        public int Size;
        public IntPtr Data;

        public static DataBlob FromBytes(byte[] bytes)
        {
            var data = Marshal.AllocHGlobal(bytes.Length);
            Marshal.Copy(bytes, 0, data, bytes.Length);
            return new DataBlob { Size = bytes.Length, Data = data };
        }

        public void Dispose()
        {
            if (Data == IntPtr.Zero) return;
            for (var index = 0; index < Size; index++) Marshal.WriteByte(Data, index, 0);
            Marshal.FreeHGlobal(Data);
            Data = IntPtr.Zero;
            Size = 0;
        }
    }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CryptProtectData(
        ref DataBlob input, string? description, IntPtr entropy, IntPtr reserved,
        IntPtr prompt, int flags, out DataBlob output);

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CryptUnprotectData(
        ref DataBlob input, IntPtr description, IntPtr entropy, IntPtr reserved,
        IntPtr prompt, int flags, out DataBlob output);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);
}
