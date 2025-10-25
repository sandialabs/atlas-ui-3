#!/usr/bin/env python3
"""
Corporate Cars (Fleet) MCP Server

Implements a simple RAG-style interface to discover data sources and retrieve
locations and metadata for corporate cars employees are using.

Tools implemented (per RAG_update.md contract):
 - rag_discover_resources(username)
 - rag_get_raw_results(username, query, sources, top_k=8, filters=None, ranking=None)
 - rag_get_synthesized_results(username, query, sources=None, top_k=None, synthesis_params=None, provided_context=None)

This is an in-memory example intended for demos and tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import time
import datetime as dt
from fastmcp import FastMCP


# Initialize the MCP server
mcp = FastMCP("CorporateCars")


# --- In-memory data model ----------------------------------------------------
@dataclass
class Car:
    vin: str
    make: str
    model: str
    year: int
    assigned_to: Optional[str]  # employee name or None for pool
    department: str  # e.g., Sales, Engineering, Exec
    region: str  # West, East, Central
    status: str  # active, maintenance, offline
    odometer_mi: int
    fuel_pct: int
    last_seen: str  # ISO timestamp
    lat: float
    lon: float
    city: str
    tags: List[str] = field(default_factory=list)


# Demo fleet
NOW = dt.datetime.now(dt.timezone.utc)

FLEET: List[Car] = [
    Car(
        vin="1HGBH41JXMN109186",
        make="Toyota",
        model="Camry",
        year=2022,
        assigned_to="Alice Johnson",
        department="Sales",
        region="West",
        status="active",
        odometer_mi=12543,
        fuel_pct=72,
        last_seen=(NOW - dt.timedelta(minutes=14)).isoformat(),
        lat=37.7749,
        lon=-122.4194,
        city="San Francisco, CA",
        tags=["sedan", "hybrid"],
    ),
    Car(
        vin="2FTRX18L1XCA12345",
        make="Ford",
        model="F-150",
        year=2021,
        assigned_to="Bob Smith",
        department="Field Ops",
        region="East",
        status="active",
        odometer_mi=40877,
        fuel_pct=54,
        last_seen=(NOW - dt.timedelta(minutes=3)).isoformat(),
        lat=40.7128,
        lon=-74.0060,
        city="New York, NY",
        tags=["truck", "4x4"],
    ),
    Car(
        vin="3N1AB7AP7GY256789",
        make="Nissan",
        model="Altima",
        year=2019,
        assigned_to=None,
        department="Pool",
        region="Central",
        status="active",
        odometer_mi=58210,
        fuel_pct=33,
        last_seen=(NOW - dt.timedelta(hours=2)).isoformat(),
        lat=41.8781,
        lon=-87.6298,
        city="Chicago, IL",
        tags=["pool", "sedan"],
    ),
    Car(
        vin="WDDGF8AB9EA123456",
        make="Mercedes-Benz",
        model="E350",
        year=2023,
        assigned_to="CEO Vehicle",
        department="Executive",
        region="West",
        status="active",
        odometer_mi=8123,
        fuel_pct=88,
        last_seen=(NOW - dt.timedelta(minutes=25)).isoformat(),
        lat=37.3382,
        lon=-121.8863,
        city="San Jose, CA",
        tags=["executive", "luxury"],
    ),
]


# Resources represent logical subsets users can search
RESOURCES: Dict[str, Dict[str, Any]] = {
    # id -> metadata + predicate
    "west_region": {
        "name": "West Region Fleet",
        "defaultSelected": False,
        "groups": ["users"],
        "predicate": lambda c: c.region == "West",
    },
    "east_region": {
        "name": "East Region Fleet",
        "defaultSelected": False,
        "groups": ["users"],
        "predicate": lambda c: c.region == "East",
    },
    "central_region": {
        "name": "Central Region Fleet",
        "defaultSelected": False,
        "groups": ["users"],
        "predicate": lambda c: c.region == "Central",
    },
    "executive_fleet": {
        "name": "Executive Fleet",
        "defaultSelected": False,
        "groups": ["executive"],
        "predicate": lambda c: "executive" in (c.tags or []),
    },
    "pool_cars": {
        "name": "Pool Cars",
        "defaultSelected": False,
        "groups": ["users"],
        "predicate": lambda c: c.assigned_to is None,
    },
}


def _start_meta() -> Tuple[float, Dict[str, Any]]:
    return time.perf_counter(), {}


def _done_meta(meta: Dict[str, Any], start: float) -> Dict[str, Any]:
    meta = dict(meta)
    meta["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return meta


def _user_default_resource(username: str) -> Optional[str]:
    # Simple demo mapping: choose default resource by known user names
    u = (username or "").strip().lower()
    if not u:
        return None
    if "alice" in u:
        return "west_region"
    if "bob" in u:
        return "east_region"
    return None


def _filter_cars_by_sources(sources: Optional[List[str]]) -> List[Car]:
    if not sources:
        return list(FLEET)
    preds = []
    for sid in sources:
        info = RESOURCES.get(sid)
        if info and callable(info.get("predicate")):
            preds.append(info["predicate"])  # type: ignore[index]
    if not preds:
        return []
    out: List[Car] = []
    for c in FLEET:
        if any(p(c) for p in preds):
            out.append(c)
    return out


def _car_to_hit(c: Car, resource_id: str, score: float, why: str) -> Dict[str, Any]:
    title = f"{c.year} {c.make} {c.model} — {c.city}"
    snippet = (
        f"VIN {c.vin}. Assigned to: {c.assigned_to or 'POOL'}. "
        f"Dept: {c.department}. Region: {c.region}. Status: {c.status}. "
        f"Last seen: {c.last_seen}. {why}"
    )
    return {
        "resourceId": resource_id,  # aggregator will qualify with server
        "title": title,
        "snippet": snippet,
        "score": score,
        "location": {
            "lat": c.lat,
            "lon": c.lon,
            "city": c.city,
        },
        "car": {
            "vin": c.vin,
            "make": c.make,
            "model": c.model,
            "year": c.year,
            "assigned_to": c.assigned_to,
            "department": c.department,
            "region": c.region,
            "status": c.status,
            "odometer_mi": c.odometer_mi,
            "fuel_pct": c.fuel_pct,
            "tags": c.tags,
        },
    }


def _search_score(query: str, c: Car) -> Tuple[float, str]:
    """Very simple scoring: count keyword matches across fields."""
    if not query:
        return 0.1, "No query provided"
    q = query.lower()
    score = 0.0
    reasons = []
    fields: List[Tuple[str, str]] = [
        ("employee", (c.assigned_to or "").lower()),
        ("city", c.city.lower()),
        ("make", c.make.lower()),
        ("model", c.model.lower()),
        ("department", c.department.lower()),
        ("region", c.region.lower()),
        ("status", c.status.lower()),
        ("vin", c.vin.lower()),
    ]
    for label, text in fields:
        if text and q in text:
            score += 1.0
            reasons.append(f"match:{label}")
    # Prefer recent last_seen slightly
    try:
        last = dt.datetime.fromisoformat(c.last_seen)
        age_min = max(0.0, (NOW - last).total_seconds() / 60.0)
        score += max(0.0, 0.5 - min(0.5, age_min / 120.0))  # up to +0.5 if < 0 min old
    except Exception:
        pass
    return score, ", ".join(reasons) or "heuristic"


# --- RAG tools ---------------------------------------------------------------
@mcp.tool
def rag_discover_resources(username: str) -> Dict[str, Any]:
    """
    Discover available fleet data sources (resources) for this server.

    Args:
        username: The current user's username (for ACL/defaults purposes)

    Returns:
        { results: { resources: [ {id, name, authRequired?, defaultSelected?} ] } }
    """
    start, meta = _start_meta()
    try:
        default_sid = _user_default_resource(username)
        resources_ui: List[Dict[str, Any]] = []
        for rid, info in RESOURCES.items():
            resources_ui.append({
                "id": rid,
                "name": info.get("name") or rid,
                # New contract: authRequired is always true, include per-resource groups
                "authRequired": True,
                "groups": list(info.get("groups", [])),
                "defaultSelected": (rid == default_sid) or bool(info.get("defaultSelected", False)),
            })
        return {
            "results": {"resources": resources_ui},
            "meta_data": _done_meta(meta, start),
        }
    except Exception as e:
        return {
            "results": {"resources": [], "error": str(e)},
            "meta_data": _done_meta(meta, start),
        }


@mcp.tool
def rag_get_raw_results(
    username: str,
    query: str,
    sources: Optional[List[str]] = None,
    top_k: int = 8,
    filters: Optional[Dict[str, Any]] = None,
    ranking: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Search fleet data and return raw hits with location and car metadata.

    Args mirror the expected contract from the aggregator. "sources" are the
    resource IDs from rag_discover_resources.
    """
    start, meta = _start_meta()
    filters = filters or {}
    try:
        cars = _filter_cars_by_sources(sources)

        # Apply simple filters
        dept = (filters.get("department") or "").lower() if isinstance(filters, dict) else ""
        status = (filters.get("status") or "").lower() if isinstance(filters, dict) else ""
        if dept:
            cars = [c for c in cars if c.department.lower() == dept]
        if status:
            cars = [c for c in cars if c.status.lower() == status]

        # Score and assemble hits
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for c in cars:
            s, why = _search_score(query or "", c)
            if s <= 0:
                continue
            # Choose the first matching source id for this car for provenance
            rid = None
            for sid, info in RESOURCES.items():
                pred = info.get("predicate")
                if callable(pred) and pred(c):
                    rid = sid
                    break
            rid = rid or (sources[0] if sources else "fleet")
            hit = _car_to_hit(c, rid, score=float(s), why=why)
            scored.append((float(s), hit))

        scored.sort(key=lambda t: t[0], reverse=True)
        hits = [h for _, h in scored[: (top_k or len(scored))]]

        return {
            "results": {
                "hits": hits,
                "stats": {"total": len(scored), "returned": len(hits)},
            },
            "meta_data": _done_meta(meta, start),
        }
    except Exception as e:
        return {
            "results": {"hits": [], "error": str(e)},
            "meta_data": _done_meta(meta, start),
        }


@mcp.tool
def rag_get_synthesized_results(
    username: str,
    query: str,
    sources: Optional[List[str]] = None,
    top_k: Optional[int] = None,
    synthesis_params: Optional[Dict[str, Any]] = None,
    provided_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return a simple synthesized answer about car locations.
    """
    start, meta = _start_meta()
    try:
        # Reuse raw results to build a succinct answer
        raw = rag_get_raw_results(
            username=username,
            query=query,
            sources=sources,
            top_k=(top_k or 5),
            filters=(synthesis_params or {}).get("filters") if synthesis_params else None,
        )
        hits = ((raw.get("results") or {}).get("hits") or [])
        if not hits:
            return {
                "results": {"answer": "No matching vehicles found.", "citations": []},
                "meta_data": _done_meta(meta, start),
            }

        # Build a sentence per hit
        lines: List[str] = []
        cits: List[Dict[str, Any]] = []
        for h in hits:
            car = (h.get("car") or {})
            loc = (h.get("location") or {})
            who = car.get("assigned_to") or "POOL"
            city = loc.get("city") or "(unknown)"
            vin = car.get("vin")
            line = f"{who}'s {car.get('year')} {car.get('make')} {car.get('model')} is in {city}."
            lines.append(line)
            cits.append({
                "resourceId": h.get("resourceId"),
                "snippet": h.get("snippet"),
                "car": car,
                "location": loc,
                "vin": vin,
            })

        answer = "\n".join(lines[: (top_k or 3)])
        return {
            "results": {
                "answer": answer,
                "citations": cits,
                "limits": {"truncated": len(lines) > (top_k or 3)},
            },
            "meta_data": _done_meta(meta, start),
        }
    except Exception as e:
        return {
            "results": {"answer": "", "error": str(e)},
            "meta_data": _done_meta(meta, start),
        }


if __name__ == "__main__":
    mcp.run()
