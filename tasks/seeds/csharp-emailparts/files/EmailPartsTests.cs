namespace EmailParts;

internal static class EmailPartsTests
{
    private static int Main()
    {
        var tests = new (string Name, Action Run)[]
        {
            (nameof(BuildsExactPlainMimePart), BuildsExactPlainMimePart),
            (nameof(BuildsExactHtmlMimePart), BuildsExactHtmlMimePart),
            (nameof(PlainPartDoesNotInterpretMarkupLookingFields), PlainPartDoesNotInterpretMarkupLookingFields),
            (nameof(PlainPartPreservesEntitySpellingsAndParagraphSpacing), PlainPartPreservesEntitySpellingsAndParagraphSpacing),
            (nameof(HtmlPartEncodesTextAndAttributeContexts), HtmlPartEncodesTextAndAttributeContexts),
            (nameof(EveryLocalizedValueUsesTheDocumentLanguage), EveryLocalizedValueUsesTheDocumentLanguage),
        };

        var failures = 0;
        foreach (var test in tests)
        {
            try
            {
                test.Run();
                Console.WriteLine($"PASS {test.Name}");
            }
            catch (Exception error)
            {
                failures++;
                Console.Error.WriteLine($"FAIL {test.Name}: {error.Message}");
            }
        }

        Console.WriteLine($"{tests.Length - failures}/{tests.Length} acceptance checks passed");
        return failures == 0 ? 0 : 1;
    }

    private static void BuildsExactPlainMimePart()
    {
        var parts = BuildOrderNotice(out _);
        Equal(ReadFixture("order-text.mime"), parts.PlainText.ToMimeFixture());
        Equal("text/plain", parts.PlainText.MediaType);
    }

    private static void BuildsExactHtmlMimePart()
    {
        var parts = BuildOrderNotice(out _);
        Equal(ReadFixture("order-html.mime"), parts.Html.ToMimeFixture());
        Equal("text/html", parts.Html.MediaType);
    }

    private static void PlainPartDoesNotInterpretMarkupLookingFields()
    {
        var localizer = new RecordingLocalizer(new Dictionary<(string, string), string>
        {
            [("en", "heading")] = "Status <ready> & waiting",
            [("en", "footer")] = "Ops <night>",
        });
        var content = new EmailContent(
            "en",
            "heading",
            new[]
            {
                new EmailParagraph(new EmailInline[]
                {
                    new TextRun("<strong>literal</strong> & unparsed"),
                    LineBreak.Instance,
                    new LinkRun("heading", "https://example.test/a&b?x=<y>"),
                }),
            },
            "footer");

        var plain = EmailPartBuilder.Build(content, localizer).PlainText.Body;
        Equal(
            "Status <ready> & waiting\r\n\r\n" +
            "<strong>literal</strong> & unparsed\r\n" +
            "Status <ready> & waiting (https://example.test/a&b?x=<y>)\r\n\r\n" +
            "Ops <night>\r\n",
            plain);
    }

    private static void PlainPartPreservesEntitySpellingsAndParagraphSpacing()
    {
        var localizer = new RecordingLocalizer(new Dictionary<(string, string), string>
        {
            [("en", "heading")] = "Entity spellings &lt;heading&gt;",
            [("en", "link_label")] = "Open &amp; inspect",
            [("en", "footer")] = "Footer &#169; &quot;literal&quot;",
        });
        var content = new EmailContent(
            "en",
            "heading",
            new[]
            {
                new EmailParagraph(new EmailInline[]
                {
                    new TextRun("Body &lt;not-a-tag&gt; and &#233;"),
                }),
                new EmailParagraph(new EmailInline[]
                {
                    new LinkRun(
                        "link_label",
                        "https://example.test/?q=&lt;literal&gt;&copy=&#169;"),
                }),
            },
            "footer");

        var plain = EmailPartBuilder.Build(content, localizer).PlainText.Body;
        Equal(
            "Entity spellings &lt;heading&gt;\r\n\r\n" +
            "Body &lt;not-a-tag&gt; and &#233;\r\n\r\n" +
            "Open &amp; inspect (https://example.test/?q=&lt;literal&gt;&copy=&#169;)\r\n\r\n" +
            "Footer &#169; &quot;literal&quot;\r\n",
            plain);
    }

    private static void HtmlPartEncodesTextAndAttributeContexts()
    {
        var localizer = new RecordingLocalizer(new Dictionary<(string, string), string>
        {
            [("en", "heading")] = "<script>alert('heading')</script>",
            [("en", "link_label")] = "<click & \"confirm\">",
            [("en", "footer")] = "Footer <unsafe> & done",
        });
        var content = new EmailContent(
            "en",
            "heading",
            new[]
            {
                new EmailParagraph(new EmailInline[]
                {
                    new TextRun("Text </p><script>alert('body')</script>"),
                    new LinkRun(
                        "link_label",
                        "https://example.test/?q=\" onclick=\"alert(1)&x=<tag>"),
                }),
            },
            "footer");

        var html = EmailPartBuilder.Build(content, localizer).Html.Body;
        Equal(
            "<!doctype html>\r\n" +
            "<html lang=\"en\">\r\n" +
            "<body>\r\n" +
            "<h1>&lt;script&gt;alert(&#39;heading&#39;)&lt;/script&gt;</h1>\r\n" +
            "<p>Text &lt;/p&gt;&lt;script&gt;alert(&#39;body&#39;)&lt;/script&gt;" +
            "<a href=\"https://example.test/?q=&quot; onclick=&quot;alert(1)&amp;x=&lt;tag&gt;\">" +
            "&lt;click &amp; &quot;confirm&quot;&gt;</a></p>\r\n" +
            "<p class=\"footer\">Footer &lt;unsafe&gt; &amp; done</p>\r\n" +
            "</body>\r\n" +
            "</html>\r\n",
            html);
    }

    private static void EveryLocalizedValueUsesTheDocumentLanguage()
    {
        BuildOrderNotice(out var localizer);
        Equal(
            new[]
            {
                "fr-CA:heading", "fr-CA:localized_body", "fr-CA:link_label", "fr-CA:footer",
                "fr-CA:heading", "fr-CA:localized_body", "fr-CA:link_label", "fr-CA:footer",
            },
            localizer.Requests);
    }

    private static BuiltEmailParts BuildOrderNotice(out RecordingLocalizer localizer)
    {
        localizer = new RecordingLocalizer(new Dictionary<(string, string), string>
        {
            [("fr-CA", "heading")] = "Bonjour <équipe> & partenaires",
            [("fr-CA", "localized_body")] = "Localized <message> & details: ",
            [("fr-CA", "link_label")] = "Voir & confirmer",
            [("fr-CA", "footer")] = "Merci, l'équipe R&D",
        });

        var content = new EmailContent(
            "fr-CA",
            "heading",
            new[]
            {
                new EmailParagraph(new EmailInline[]
                {
                    new LocalizedRun("localized_body"),
                    new TextRun("Votre commande <A&B> est prête."),
                    LineBreak.Instance,
                    new LinkRun(
                        "link_label",
                        "https://shop.example/orders/A&B?lang=fr-CA&mode=full"),
                }),
            },
            "footer");

        return EmailPartBuilder.Build(content, localizer);
    }

    private static string ReadFixture(string name)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", name);
        return File.ReadAllText(path).ReplaceLineEndings("\r\n");
    }

    private static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException(
                $"Expected {Display(expected)}, got {Display(actual)}");
        }
    }

    private static void Equal<T>(IReadOnlyList<T> expected, IReadOnlyList<T> actual)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new InvalidOperationException(
                $"Expected [{string.Join(", ", expected)}], got [{string.Join(", ", actual)}]");
        }
    }

    private static string Display<T>(T value) =>
        value?.ToString()?.Replace("\r", "\\r").Replace("\n", "\\n") ?? "<null>";

    private sealed class RecordingLocalizer(
        IReadOnlyDictionary<(string Language, string Key), string> values) : IEmailLocalizer
    {
        public List<string> Requests { get; } = new();

        public string Get(string language, string key)
        {
            Requests.Add($"{language}:{key}");
            return values[(language, key)];
        }
    }
}
