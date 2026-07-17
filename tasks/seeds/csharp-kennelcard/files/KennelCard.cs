using System.Globalization;

namespace PinesKennel;

/// <summary>A boarding stay as the front desk enters it.</summary>
public sealed record Booking(
    string Pet,
    string Owner,
    string Run,
    string Food,
    double MealCups,
    IReadOnlyList<string> Meds,
    bool Hold);

/// <summary>
/// Renders the paper run card we clip to each kennel door so the
/// afternoon crew knows who eats what and who stays put.
/// </summary>
public static class KennelCard
{
    public static string Render(Booking booking)
    {
        var lines = new List<string>();
        lines.Add(Header(booking));
        lines.Add(FeedLine(booking.Food, booking.MealCups));
        foreach (var med in booking.Meds)
        {
            lines.Add("MED: " + med);
        }
        if (booking.Hold)
        {
            lines.Add(HoldBanner());
        }
        return string.Join("\n", lines);
    }

    private static string Header(Booking booking)
    {
        return string.Format(CultureInfo.InvariantCulture,
            "RUN {0} — {1} ({2})", booking.Run, booking.Pet, booking.Owner);
    }

    private static string HoldBanner()
    {
        return @"** DO NOT RELEASE — the desk marked this stay ""HOLD"";
    }

    private static string FeedLine(string food, double cups)
    {
        return string.Format(CultureInfo.InvariantCulture, "{0}, {1:0.0} cups, morning and evening", food, cups);
    }
}
