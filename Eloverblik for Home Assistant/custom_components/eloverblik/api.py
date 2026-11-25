"""A small async client for the Eloverblik customer API (api.eloverblik.dk/customerapi).

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor:
the pyeloverblik/requests dependency is replaced by this client on Home Assistant's own
aiohttp session. Flow: the refresh token from eloverblik.dk buys a 24-hour access token at
/token, which then reads the metering points and their hourly time series.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import aiohttp

BASE_URL = "https://api.eloverblik.dk/customerapi/api"
ACCESS_TOKEN_LIFETIME = 23 * 3600

QUALITY_MEASURED = 0
QUALITY_ESTIMATED = 1
QUALITY_INCOMPLETE = 2
QUALITY_MISSING = 3


class EloverblikError(Exception):
    """Something went wrong talking to Eloverblik."""


class EloverblikAuthError(EloverblikError):
    """The refresh token was not accepted."""


class EloverblikRateLimitError(EloverblikError):
    """Too many requests (429) or DataHub busy (503): try again later."""


_EXPLAIN = {
    10007: "you haven't allowed a CPR lookup; allow it on eloverblik.dk",
    20008: "the meter was not found",
    20010: "this meter isn't linked to you yet",
    20012: "you're not allowed to read this meter",
    30010: "your access doesn't cover the whole period",
    30014: "the period is not allowed (longer than 730 days, or it ends before it starts)",
    30015: "there are no readings for this period",
    30016: "your access to this meter has expired",
    30017: "there are no readings that far back for this meter",
    30018: "there are no readings that far back for this meter",
    40014: "no authorization was found for this meter",
    50000: "this isn't a Customer API key",
    50001: "the key is not valid or has been switched off",
    50002: "Eloverblik could not create access from this key",
}


def explain(code: int, fallback: str = "") -> str:
    """Plain-language meaning of a documented Eloverblik error code."""
    return _EXPLAIN.get(code) or (fallback.strip() or "unknown error")


def quality_from_code(code: str) -> int:
    return {"A03": QUALITY_ESTIMATED, "A05": QUALITY_INCOMPLETE, "A02": QUALITY_MISSING}.get(
        (code or "").strip(), QUALITY_MEASURED
    )


@dataclass
class Meter:
    """One metering point."""

    id: str
    kind: str = ""
    street: str = ""
    postcode: str = ""
    city: str = ""
    supplier: str = ""
    has_relation: bool = True
    start_date: str = ""

    @property
    def short_id(self) -> str:
        digits = "".join(c for c in self.id if c.isalnum())
        return digits[-6:].lower()

    @property
    def is_production(self) -> bool:
        return self.kind == "E18"

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def address(self) -> str:
        line2 = f"{self.postcode} {self.city}".strip()
        parts = [p for p in (self.street.strip(), line2) if p]
        return ", ".join(parts) if parts else f"Meter …{self.short_id}"


def kind_label(kind: str) -> str:
    if kind == "E17":
        return "Consumption"
    if kind == "E18":
        return "Production"
    if kind == "E20":
        return "Exchange"
    if kind.startswith("D"):
        return "Sub-meter"
    return "Meter"


@dataclass
class MeterData:
    """Hourly readings for one meter from one time series call."""

    id: str = ""
    unit: str = ""
    # hour start (Unix seconds, UTC) -> (kWh, quality)
    hours: dict[int, tuple[float, int]] = field(default_factory=dict)
    # A per-meter problem Eloverblik reported (None when fine or simply empty).
    problem: str | None = None


def _s(obj: dict, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def parse_time(text: str) -> datetime | None:
    """Parse Eloverblik's UTC times, e.g. 2026-11-01T23:00:00Z or 2026-11-01T23:00Z."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_RESOLUTION_SECONDS = {"PT15M": 900, "PT30M": 1800, "PT1H": 3600, "PT60M": 3600}


def parse_metering_points(payload: Any) -> list[Meter]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, list):
        raise EloverblikError("Eloverblik sent no list of meters")
    meters: list[Meter] = []
    seen: set[str] = set()
    for item in result:
        if not isinstance(item, dict):
            continue
        meter_id = _s(item, "meteringPointId")
        if not meter_id or meter_id in seen:
            continue
        seen.add(meter_id)
        floor, room = _s(item, "floorId"), _s(item, "roomId")
        unit = " ".join(p for p in ((f"{floor}." if floor else ""), room) if p)
        street = " ".join(p for p in (_s(item, "streetName"), _s(item, "buildingNumber"), unit) if p)
        has_relation = item.get("hasRelation")
        meters.append(
            Meter(
                id=meter_id,
                kind=_s(item, "typeOfMP"),
                street=street,
                postcode=_s(item, "postcode"),
                city=_s(item, "cityName"),
                supplier=_s(item, "balanceSupplierName"),
                has_relation=True if has_relation is None else bool(has_relation),
                start_date=_s(item, "consumerStartDate"),
            )
        )
    meters.sort(key=lambda m: (not m.has_relation, m.kind, m.id))
    return meters


def parse_time_series(payload: Any) -> list[MeterData]:
    """Turn a gettimeseries answer into hourly readings per meter (15-minute data is summed)."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, list):
        raise EloverblikError("Eloverblik sent no result list in the readings")
    out: list[MeterData] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        data = MeterData(id=_s(item, "id"))
        success = item.get("success", True)
        code = item.get("errorCode", 10000)
        code = code if isinstance(code, int) else 10000
        if success is False and code != 10000:
            if code != 30015:  # "no data" is not a problem
                data.problem = explain(code, _s(item, "errorText"))
            out.append(data)
            continue
        doc = item.get("MyEnergyData_MarketDocument") or {}
        for series in doc.get("TimeSeries") or []:
            if not isinstance(series, dict):
                continue
            if _s(series, "mRID"):
                data.id = _s(series, "mRID")
            if _s(series, "measurement_Unit.name"):
                data.unit = _s(series, "measurement_Unit.name")
            for period in series.get("Period") or []:
                if not isinstance(period, dict):
                    continue
                step = _RESOLUTION_SECONDS.get(_s(period, "resolution"))
                start = parse_time(_s(period.get("timeInterval") or {}, "start"))
                if step is None or start is None:
                    continue
                start_ts = int(start.timestamp())
                for point in period.get("Point") or []:
                    try:
                        position = int(_s(point, "position"))
                        kwh = float(_s(point, "out_Quantity.quantity").replace(",", "."))
                    except ValueError:
                        continue
                    if position < 1:
                        continue
                    quality = quality_from_code(_s(point, "out_Quantity.quality"))
                    ts = start_ts + (position - 1) * step
                    hour = ts - ts % 3600
                    old_kwh, old_quality = data.hours.get(hour, (0.0, QUALITY_MEASURED))
                    data.hours[hour] = (old_kwh + kwh, max(old_quality, quality))
        out.append(data)
    return out


class EloverblikClient:
    """Talks to the Eloverblik customer API with Home Assistant's aiohttp session."""

    def __init__(self, session: aiohttp.ClientSession, refresh_token: str) -> None:
        self._session = session
        self._refresh_token = refresh_token.strip()
        self._access_token: str | None = None
        self._access_token_at = 0.0

    async def _request(
        self, method: str, path: str, token: str, body: Any = None, on_token: bool = False
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "api-version": "1.0",
        }
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status == 401 and on_token:
                    raise EloverblikAuthError(
                        "Eloverblik did not accept the key. It may have expired or been switched off: create a new one on eloverblik.dk."
                    )
                if resp.status == 403 and on_token:
                    raise EloverblikAuthError(
                        "This key is not a Customer API key. Create the token under your own login on eloverblik.dk."
                    )
                if resp.status == 401:
                    raise _AccessExpired
                if resp.status in (429, 503):
                    raise EloverblikRateLimitError(
                        "Eloverblik is busy or got too many requests; trying again later."
                    )
                if resp.status >= 400:
                    text = (await resp.text())[:200]
                    raise EloverblikError(f"Eloverblik answered with error {resp.status}: {text}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EloverblikError(f"Could not reach Eloverblik: {err}") from err

    async def async_get_access_token(self, force: bool = False) -> str:
        """A 24-hour access token, reused while fresh (the token endpoint allows 2 calls a minute)."""
        if (
            not force
            and self._access_token
            and time.monotonic() - self._access_token_at < ACCESS_TOKEN_LIFETIME
        ):
            return self._access_token
        payload = await self._request("GET", "/token", self._refresh_token, on_token=True)
        token = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            code = payload.get("errorCode", 0) if isinstance(payload, dict) else 0
            raise EloverblikAuthError(
                f"Eloverblik did not give access: {explain(code, str(payload.get('errorText', '')) if isinstance(payload, dict) else '')}"
            )
        self._access_token = token.strip()
        self._access_token_at = time.monotonic()
        return self._access_token

    async def _with_access(self, method: str, path: str, body: Any = None) -> Any:
        token = await self.async_get_access_token()
        try:
            return await self._request(method, path, token, body)
        except _AccessExpired:
            token = await self.async_get_access_token(force=True)
            try:
                return await self._request(method, path, token, body)
            except _AccessExpired as err:
                raise EloverblikAuthError("Eloverblik refused access to the data.") from err

    async def async_get_metering_points(self) -> list[Meter]:
        payload = await self._with_access("GET", "/meteringpoints/meteringpoints?includeAll=true")
        return parse_metering_points(payload)

    async def async_get_time_series(
        self, meter_ids: list[str], date_from: date, date_to: date
    ) -> list[MeterData]:
        """Hourly readings between two Danish calendar days (end exclusive, at most 730 days)."""
        path = f"/meterdata/gettimeseries/{date_from:%Y-%m-%d}/{date_to:%Y-%m-%d}/Hour"
        payload = await self._with_access(
            "POST", path, {"meteringPoints": {"meteringPoint": list(meter_ids)}}
        )
        if isinstance(payload, dict) and not isinstance(payload.get("result"), list):
            code = payload.get("errorCode")
            if isinstance(code, int) and code != 10000:
                raise EloverblikError(f"Eloverblik error {code}: {explain(code, str(payload.get('errorText', '')))}")
        return parse_time_series(payload)


class _AccessExpired(Exception):
    """The 24-hour access token ran out (401 on a data call)."""
