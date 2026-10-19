"""Ціль подушки: шість середніх повних місяців, без прогнозу неповного."""

import calendar
from decimal import Decimal, ROUND_HALF_UP


def target(results, through):
    months = [row for row in results if row[0].replace(
        day=calendar.monthrange(row[0].year, row[0].month)[1]) <= through]
    if not months:
        return None
    average = sum((row[2] for row in months), Decimal(0)) / len(months)
    if average <= 0:
        return None
    return {"average": average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "target": (average * 6).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "months": len(months)}
