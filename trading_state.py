from datetime import datetime, timedelta
from typing import Dict, Any


class VixBotState:
    def __init__(self):
        self.hook_detected = False
        self.is_hooked = False
        self.breakout_level = 0.0
        self.arm_time = None
        self.wait_until = None
        self.fresh_search_ready = False
        self.gap_direction = "NEUTRAL"

    def reset_daily(self):
        """Daily reset at 9:15"""
        self.hook_detected = False
        self.is_hooked = False
        self.breakout_level = 0.0
        self.arm_time = None
        self.wait_until = None
        self.fresh_search_ready = False


# Global state
bot_state = VixBotState()
