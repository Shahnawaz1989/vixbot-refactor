from .half_day_targets import calculate as half_day
from .normal_day_targets import calculate as normal_day


def route_flow(session_type):
    return {
        "half": half_day,
        "normal": normal_day
    }.get(session_type, normal_day)()
