from __future__ import annotations
import calendar
import hashlib
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class Fixture:
    id: str
    title: str
    description: str
    source: str
    reference: str
    example_input: dict[str, Any]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.encode()).hexdigest()


FIXTURES = {
    "month-end": Fixture(
        "month-end", "Month-end scheduling", "Scheduling January 31 one month ahead crashes. December also fails to roll into the next year. Preserve the day when possible; otherwise use the target month's last day.",
        "from datetime import date\n\ndef solve(args):\n    current = date.fromisoformat(args['date'])\n    return date(current.year, current.month + args['months'], current.day).isoformat()\n",
        "import calendar\nfrom datetime import date\n\ndef solve(args):\n    current = date.fromisoformat(args['date'])\n    absolute = current.year * 12 + current.month - 1 + args['months']\n    year, month0 = divmod(absolute, 12)\n    day = min(current.day, calendar.monthrange(year, month0 + 1)[1])\n    return date(year, month0 + 1, day).isoformat()\n",
        {"date": "2026-01-31", "months": 1},
    ),
    "rounding": Fixture(
        "rounding", "Discount rounding", "The checkout drops fractional cents instead of rounding half up after applying the discount to the complete basket. Input prices are integer cents and discount_bps is basis points.",
        "def solve(args):\n    total = sum(item['unit_minor'] * item['quantity'] for item in args['items'])\n    return int(total * (1 - args['discount_bps'] / 10000))\n",
        "from decimal import Decimal, ROUND_HALF_UP\n\ndef solve(args):\n    total = sum(item['unit_minor'] * item['quantity'] for item in args['items'])\n    discounted = Decimal(total) * (Decimal(10000 - args['discount_bps']) / Decimal(10000))\n    return int(discounted.quantize(Decimal('1'), rounding=ROUND_HALF_UP))\n",
        {"items": [{"unit_minor": 299, "quantity": 1}], "discount_bps": 1000},
    ),
    "retry-events": Fixture(
        "retry-events", "Webhook retries", "A retried event is counted twice. Apply each event ID only once, preserve the first occurrence and report duplicate IDs once in order of first duplicate.",
        "def solve(args):\n    return {'total': sum(e['delta'] for e in args['events']), 'duplicates': []}\n",
        "def solve(args):\n    seen = set()\n    duplicates = []\n    total = 0\n    for event in args['events']:\n        if event['id'] in seen:\n            if event['id'] not in duplicates:\n                duplicates.append(event['id'])\n        else:\n            seen.add(event['id'])\n            total += event['delta']\n    return {'total': total, 'duplicates': duplicates}\n",
        {"events": [{"id": "a", "delta": 7}, {"id": "a", "delta": 7}]},
    ),
}


def expected(fixture_id: str, args: dict[str, Any]) -> Any:
    if fixture_id == "month-end":
        current = date.fromisoformat(args["date"])
        offset = current.month - 1 + args["months"]
        year = current.year + offset // 12
        month = offset % 12 + 1
        return date(year, month, min(current.day, calendar.monthrange(year, month)[1])).isoformat()
    if fixture_id == "rounding":
        amount = sum(Decimal(item["unit_minor"]) * item["quantity"] for item in args["items"])
        return int((amount * (Decimal(1) - Decimal(args["discount_bps"]) / 10000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if fixture_id == "retry-events":
        first: dict[str, int] = {}
        duplicates: list[str] = []
        for event in args["events"]:
            event_id = event["id"]
            if event_id in first:
                if event_id not in duplicates:
                    duplicates.append(event_id)
            else:
                first[event_id] = event["delta"]
        return {"total": sum(first.values()), "duplicates": duplicates}
    raise ValueError("Unknown fixture")


def cases(fixture_id: str, seed: int) -> list[dict[str, Any]]:
    fixture = FIXTURES[fixture_id]
    rng = random.Random(seed)
    values = [fixture.example_input]
    if fixture_id == "month-end":
        values.extend([{"date": "2026-12-31", "months": 1}, {"date": "2028-01-31", "months": 1}, {"date": "2028-02-29", "months": 12}])
        for _ in range(16):
            year, month = rng.randrange(2020, 2035), rng.randrange(1, 13)
            day = calendar.monthrange(year, month)[1]
            values.append({"date": date(year, month, day).isoformat(), "months": rng.randrange(1, 25)})
    elif fixture_id == "rounding":
        values.extend([{"items": [{"unit_minor": 1, "quantity": 1}], "discount_bps": 5000}, {"items": [{"unit_minor": 199, "quantity": 3}], "discount_bps": 0}])
        for _ in range(17):
            values.append({"items": [{"unit_minor": rng.randrange(1, 10000), "quantity": rng.randrange(1, 8)} for _ in range(rng.randrange(1, 4))], "discount_bps": rng.randrange(0, 10001)})
    else:
        values.extend([{"events": []}, {"events": [{"id": "x", "delta": 4}, {"id": "x", "delta": 99}, {"id": "y", "delta": -1}]}])
        for _ in range(17):
            events = [{"id": str(rng.randrange(1, 5)), "delta": rng.randrange(-10, 20)} for _ in range(rng.randrange(1, 9))]
            values.append({"events": events})
    return values
