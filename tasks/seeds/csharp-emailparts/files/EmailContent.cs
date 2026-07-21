namespace EmailParts;

/// <summary>Localized, structured content shared by both MIME alternatives.</summary>
public sealed record EmailContent(
    string Language,
    string HeadingKey,
    IReadOnlyList<EmailParagraph> Paragraphs,
    string FooterKey);

public sealed record EmailParagraph(IReadOnlyList<EmailInline> Inlines);

public abstract record EmailInline;

/// <summary>Ordinary application text. It is never markup.</summary>
public sealed record TextRun(string Value) : EmailInline;

/// <summary>A resource key resolved using <see cref="EmailContent.Language"/>.</summary>
public sealed record LocalizedRun(string Key) : EmailInline;

/// <summary>A localized, clickable label and its destination.</summary>
public sealed record LinkRun(string LabelKey, string Url) : EmailInline;

/// <summary>An author-inserted line break inside a paragraph.</summary>
public sealed record LineBreak : EmailInline
{
    public static LineBreak Instance { get; } = new();
}

public interface IEmailLocalizer
{
    string Get(string language, string key);
}

public sealed record MimePart(string MediaType, string Body)
{
    /// <summary>Stable fixture representation used by the mail assembly layer.</summary>
    public string ToMimeFixture() =>
        $"Content-Type: {MediaType}; charset=utf-8\r\n" +
        "Content-Transfer-Encoding: 8bit\r\n" +
        "\r\n" +
        Body;
}

public sealed record BuiltEmailParts(MimePart PlainText, MimePart Html);
