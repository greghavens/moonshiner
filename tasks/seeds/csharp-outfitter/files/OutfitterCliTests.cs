namespace Outfitter;

// Regression suite for the front-desk console as it ships today. These
// behaviors are live at three stores — they must keep passing unchanged.
public class ReceiveAndSellTests
{
    [Fact]
    public void ReceiveAccumulatesStock()
    {
        var cli = new OutfitterCli();
        Assert.Equal("ok: kayak-solo now 4", cli.Run("receive kayak-solo 4"));
        Assert.Equal("ok: kayak-solo now 6", cli.Run("receive kayak-solo 2"));
    }

    [Fact]
    public void SellDecrementsStock()
    {
        var cli = new OutfitterCli();
        cli.Run("receive kayak-solo 3");
        Assert.Equal("ok: kayak-solo now 1", cli.Run("sell kayak-solo 2"));
    }

    [Fact]
    public void OversellIsRefusedAndStockIsUnchanged()
    {
        var cli = new OutfitterCli();
        cli.Run("receive kayak-solo 2");
        Assert.Equal("error: only 2 of kayak-solo available", cli.Run("sell kayak-solo 5"));
        Assert.Equal("kayak-solo 2", cli.Run("stock"));
    }

    [Fact]
    public void SellingAnUnknownSkuFails()
    {
        var cli = new OutfitterCli();
        cli.Run("receive kayak-solo 2");
        Assert.Equal("error: unknown sku 'paddle-sup'", cli.Run("sell paddle-sup 1"));
    }

    [Fact]
    public void QuantityMustBeAPositiveInteger()
    {
        var cli = new OutfitterCli();
        Assert.Equal("error: quantity must be a positive integer", cli.Run("receive kayak-solo zero"));
        Assert.Equal("error: quantity must be a positive integer", cli.Run("receive kayak-solo -3"));
        cli.Run("receive kayak-solo 2");
        Assert.Equal("error: quantity must be a positive integer", cli.Run("sell kayak-solo 0"));
    }

    [Fact]
    public void UsageAndUnknownCommandErrorsAreSpecific()
    {
        var cli = new OutfitterCli();
        Assert.Equal("error: usage: receive <sku> <qty>", cli.Run("receive kayak-solo"));
        Assert.Equal("error: usage: sell <sku> <qty>", cli.Run("sell kayak-solo"));
        Assert.Equal("error: empty command", cli.Run(""));
        Assert.Equal("error: unknown command 'paddle'", cli.Run("paddle faster"));
    }
}

public class StockReportTests
{
    [Fact]
    public void StockListsSkusInOrdinalOrder()
    {
        var cli = new OutfitterCli();
        cli.Run("receive kayak-solo 4");
        cli.Run("receive PFD-adult 3");
        cli.Run("receive canoe-16 2");

        Assert.Equal("PFD-adult 3\ncanoe-16 2\nkayak-solo 4", cli.Run("stock"));
    }

    [Fact]
    public void EmptyStockRoomSaysSo()
    {
        var cli = new OutfitterCli();
        Assert.Equal("(empty)", cli.Run("stock"));
    }
}
