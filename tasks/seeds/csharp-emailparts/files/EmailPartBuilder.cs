using System.Net;
using System.Text;

namespace EmailParts;

public static class EmailPartBuilder
{
    public static BuiltEmailParts Build(EmailContent content, IEmailLocalizer localizer)
    {
        ArgumentNullException.ThrowIfNull(content);
        ArgumentNullException.ThrowIfNull(localizer);

        return new BuiltEmailParts(
            new MimePart("text/plain", RenderPlainText(content, localizer)),
            new MimePart("text/html", RenderHtml(content, localizer)));
    }

    private static string RenderPlainText(EmailContent content, IEmailLocalizer localizer)
    {
        var output = new StringBuilder();
        AppendPlainValue(output, localizer.Get(content.Language, content.HeadingKey));
        output.Append("\r\n\r\n");

        for (var paragraphIndex = 0; paragraphIndex < content.Paragraphs.Count; paragraphIndex++)
        {
            if (paragraphIndex > 0)
            {
                output.Append("\r\n\r\n");
            }

            foreach (var inline in content.Paragraphs[paragraphIndex].Inlines)
            {
                switch (inline)
                {
                    case TextRun text:
                        AppendPlainValue(output, text.Value);
                        break;
                    case LocalizedRun localized:
                        AppendPlainValue(output, localizer.Get(content.Language, localized.Key));
                        break;
                    case LinkRun link:
                        AppendPlainValue(output, localizer.Get(content.Language, link.LabelKey));
                        output.Append(" (");
                        AppendPlainValue(output, link.Url);
                        output.Append(')');
                        break;
                    case LineBreak:
                        output.Append("\r\n");
                        break;
                    default:
                        throw new InvalidOperationException($"Unknown inline type {inline.GetType().Name}");
                }
            }
        }

        output.Append("\r\n\r\n");
        AppendPlainValue(output, localizer.Get(content.Language, content.FooterKey));
        return output.Append("\r\n").ToString();
    }

    private static void AppendPlainValue(StringBuilder output, string value)
    {
        output.Append(WebUtility.HtmlEncode(value));
    }

    private static string RenderHtml(EmailContent content, IEmailLocalizer localizer)
    {
        var output = new StringBuilder();
        output.Append("<!doctype html>\r\n<html lang=\"");
        AppendHtml(output, content.Language);
        output.Append("\">\r\n<body>\r\n<h1>");
        AppendHtml(output, localizer.Get(content.Language, content.HeadingKey));
        output.Append("</h1>\r\n");

        foreach (var paragraph in content.Paragraphs)
        {
            output.Append("<p>");
            foreach (var inline in paragraph.Inlines)
            {
                switch (inline)
                {
                    case TextRun text:
                        AppendHtml(output, text.Value);
                        break;
                    case LocalizedRun localized:
                        AppendHtml(output, localizer.Get(content.Language, localized.Key));
                        break;
                    case LinkRun link:
                        output.Append("<a href=\"");
                        AppendHtml(output, link.Url);
                        output.Append("\">");
                        AppendHtml(output, localizer.Get(content.Language, link.LabelKey));
                        output.Append("</a>");
                        break;
                    case LineBreak:
                        output.Append("<br>\r\n");
                        break;
                    default:
                        throw new InvalidOperationException($"Unknown inline type {inline.GetType().Name}");
                }
            }
            output.Append("</p>\r\n");
        }

        output.Append("<p class=\"footer\">");
        AppendHtml(output, localizer.Get(content.Language, content.FooterKey));
        output.Append("</p>\r\n</body>\r\n</html>\r\n");
        return output.ToString();
    }

    private static void AppendHtml(StringBuilder output, string value)
    {
        output.Append(WebUtility.HtmlEncode(value));
    }
}
