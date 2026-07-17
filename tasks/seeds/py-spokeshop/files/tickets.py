"""Service tickets: plain repairs and warranty claims."""


class Ticket:
    def __init__(self, order_id, opened_on):
        self.order_id = order_id
        self.opened_on = opened_on
        self.closed = False

    def close(self):
        self.closed = True

    def summary(self):
        state = "closed" if self.closed else "open"
        return f"#{self.order_id} opened {self.opened_on} ({state})"


class WarrantyTicket(Ticket):
    def __init__(self, order_id, opened_on, claim_no):
        super().__init__(order_id, opened_on, claim_no)

    def summary(self):
        return f"{super().summary()} [warranty {self.claim_no}]"
