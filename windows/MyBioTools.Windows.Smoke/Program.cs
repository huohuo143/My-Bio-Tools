using MyBioTools.Windows;

try
{
    OmicsDatabaseUnlocker.ValidateRuntime();
    Console.WriteLine("PASS .NET AES-256-CTR omics runtime self-test");
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception);
    return 1;
}
