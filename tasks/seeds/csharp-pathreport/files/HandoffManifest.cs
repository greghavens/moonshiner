using System.Text;

namespace CourierDesk;

/// <summary>
/// Renders the plain-text handoff manifest that gets printed and taped to
/// each media crate for the night courier. The courier's label tool reads
/// this same file, so the layout is a contract.
/// </summary>
public static class HandoffManifest
{
    public static string Render(string client, string jobCode, IReadOnlyList<string> files)
    {
        if (files.Count == 0)
        {
            throw new ArgumentException("a crate manifest needs at least one file", nameof(files));
        }

        var sb = new StringBuilder();
        sb.Append($"HANDOFF {client} / job {jobCode}\n");
        sb.Append($"Drop folder: D:\nightly\tickets\reports-{jobCode}\n");
        sb.Append(@"Read """"HANDLING NOTES.txt"""" before loading." + "\n");
        var n = 1;
        foreach (var file in files)
        {
            sb.Append($"  [{n}] {file}\n");
            n++;
        }
        sb.Append($"Label template: {0} of {files.Count}\n");
        return sb.ToString();
    }
}
