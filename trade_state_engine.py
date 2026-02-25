from datetime import date


class TradingState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.current_date = date.today()
        self.buy_done = False
        self.sell_done = False
        self.active_position = None
        self.entry_price = None
        self.option_symbol = None

    def check_date_reset(self):
        if date.today() != self.current_date:
            self.reset()

    def can_enter(self, side):
        if self.active_position is not None:
            return False
        if side == "BUY" and self.buy_done:
            return False
        if side == "SELL" and self.sell_done:
            return False
        return True

    def mark_entry(self, side, entry_price, symbol):
        self.active_position = side
        self.entry_price = entry_price
        self.option_symbol = symbol

    def mark_exit(self):
        if self.active_position == "BUY":
            self.buy_done = True
        elif self.active_position == "SELL":
            self.sell_done = True

        self.active_position = None
        self.entry_price = None
        self.option_symbol = None
