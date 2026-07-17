"""Labor rate cards for the repair counter, cents per job."""


class RateCard:
    WEEKEND = dict(BASE, surcharge=1500)
    BASE = {"tune": 6500, "flat": 1200, "overhaul": 18000, "surcharge": 0}

    def quote(self, job, weekend=False):
        """Quoted labor in cents; unknown jobs raise KeyError."""
        card = self.WEEKEND if weekend else self.BASE
        return card[job] + card["surcharge"]
