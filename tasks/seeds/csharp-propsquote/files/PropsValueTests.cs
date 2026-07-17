using PropsQuote;

public class PropsValueTests
{
    // -- Quote: bare pass-through ------------------------------------------

    [Theory]
    [InlineData("5m")]
    [InlineData("obj/Debug/net10.0")]
    [InlineData("x:y")]
    [InlineData("a+b")]
    [InlineData("1.2.3-rc.1")]
    [InlineData("snake_case")]
    public void SafeValuesStayBare(string value)
    {
        Assert.Equal(value, PropsValue.Quote(value));
    }

    // -- Quote: pinned quoted forms ----------------------------------------

    [Theory]
    [InlineData("", "\"\"")]
    [InlineData("out dir", "\"out dir\"")]
    [InlineData("a#b", "\"a#b\"")]
    [InlineData("a=b", "\"a=b\"")]
    [InlineData("say \"when\"", "\"say \\\"when\\\"\"")]
    [InlineData("tab\tsep", "\"tab\\tsep\"")]
    [InlineData("line1\nline2", "\"line1\\nline2\"")]
    [InlineData("cr\rlf", "\"cr\\rlf\"")]
    [InlineData("${var}", "\"${var}\"")]
    [InlineData("trailing space ", "\"trailing space \"")]
    public void QuotedFormsArePinned(string value, string expected)
    {
        Assert.Equal(expected, PropsValue.Quote(value));
    }

    [Fact]
    public void WindowsPathsDoubleTheirBackslashes()
    {
        Assert.Equal(@"""C:\\tools\\bin""", PropsValue.Quote(@"C:\tools\bin"));
    }

    [Fact]
    public void OtherControlCharactersUseUppercaseUnicodeEscapes()
    {
        Assert.Equal("\"\\u0007\"", PropsValue.Quote("\a"));
        Assert.Equal("\"\\u001B[0m\"", PropsValue.Quote("\u001b[0m"));
        Assert.Equal("\"\\u007F\"", PropsValue.Quote("\u007f"));
    }

    [Fact]
    public void NonAsciiTextIsKeptVerbatimInsideQuotes()
    {
        Assert.Equal("\"café — 7¢\"", PropsValue.Quote("café — 7¢"));
    }

    // -- Unquote: pinned results -------------------------------------------

    [Theory]
    [InlineData("\"\"", "")]
    [InlineData("\"out dir\"", "out dir")]
    [InlineData("\"say \\\"when\\\"\"", "say \"when\"")]
    [InlineData("\"a\\\\b\"", "a\\b")]
    [InlineData("\"a\\tb\\nc\\rd\"", "a\tb\nc\rd")]
    [InlineData("\"\\u0041\\u00e9\"", "Aé")]
    [InlineData("\"\\u001B\"", "\u001b")]
    [InlineData("plainbare", "plainbare")]
    [InlineData("obj/Debug", "obj/Debug")]
    public void UnquotedResultsArePinned(string text, string expected)
    {
        Assert.Equal(expected, PropsValue.Unquote(text));
    }

    // -- Unquote: malformed input ------------------------------------------

    [Theory]
    [InlineData("\"unterminated")]
    [InlineData("\"a\" trailing")]
    [InlineData("\"a\"\"")]
    [InlineData("\"bad\\qescape\"")]
    [InlineData("\"dangling\\")]
    [InlineData("\"short\\u12\"")]
    [InlineData("\"nothex\\uZZZZ\"")]
    [InlineData("two words")]
    [InlineData("")]
    [InlineData("half\"quoted")]
    [InlineData("a#b")]
    public void MalformedTextIsRejected(string text)
    {
        Assert.Throws<FormatException>(() => PropsValue.Unquote(text));
    }

    [Fact]
    public void RawControlCharactersInsideQuotesAreRejected()
    {
        Assert.Throws<FormatException>(() => PropsValue.Unquote("\"a\tb\""));
        Assert.Throws<FormatException>(() => PropsValue.Unquote("\"a\nb\""));
    }

    [Fact]
    public void NullsAreArgumentErrorsNotFormatErrors()
    {
        Assert.Throws<ArgumentNullException>(() => PropsValue.Quote(null!));
        Assert.Throws<ArgumentNullException>(() => PropsValue.Unquote(null!));
    }

    // -- the round-trip law --------------------------------------------------

    [Theory]
    [InlineData("")]
    [InlineData("plain")]
    [InlineData("two words")]
    [InlineData("C:\\tools\\new folder")]
    [InlineData("say \"when\"")]
    [InlineData("tab\tsep")]
    [InlineData("line1\nline2")]
    [InlineData("crlf\r\n")]
    [InlineData("\u0001\u001f")]
    [InlineData("café — 7¢ ☕")]
    [InlineData("trailing space ")]
    [InlineData(" leading")]
    [InlineData("#comment-looking")]
    [InlineData("a=b")]
    [InlineData("${var}")]
    [InlineData("100%")]
    public void RoundTripLawHolds(string value)
    {
        Assert.Equal(value, PropsValue.Unquote(PropsValue.Quote(value)));
    }
}
