class GannCalculator:
    def __init__(self, spot, atr, vix):
        self.spot = spot
        self.atr = atr
        self.vix = vix

    def gann_target(self, level=0.618):
        return self.spot + (self.atr * level * (1 + self.vix/100))
