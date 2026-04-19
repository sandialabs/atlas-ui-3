"""Example analysis over ATLAS OpenTelemetry spans.

Run against ``logs/spans.jsonl`` to compute T&E-relevant metrics:

- Tool success rate by tool
- p50 / p95 LLM call latency by model
- RAG retrieval-to-use ratio per data source
- Retries per turn

Usage:
    python docs/telemetry/analysis_example.py [PATH_TO_SPANS_JSONL]

Defaults to ``logs/spans.jsonl`` under the project root.

Requires: pandas (install via ``uv pip install pandas``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def load_spans(path: Path) -> pd.DataFrame:
    """Load one-line-per-span JSONL into a flat DataFrame.

    Span attributes are promoted into top-level columns prefixed with ``attr_``.
    """
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    if not records:
        return pd.DataFrame()

    flat: List[Dict[str, Any]] = []
    for rec in records:
        row = {k: v for k, v in rec.items() if k != "attributes"}
        for attr_key, attr_val in (rec.get("attributes") or {}).items():
            row[f"attr_{attr_key}"] = attr_val
        flat.append(row)

    df = pd.DataFrame(flat)
    if "duration_ns" in df.columns:
        df["duration_ms"] = df["duration_ns"] / 1_000_000.0
    return df


def tool_success_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Count calls and success rate per tool."""
    tool_df = df[df["name"] == "tool.call"].copy()
    if tool_df.empty:
        return pd.DataFrame(columns=["tool_name", "call_count", "success_rate", "p95_ms"])
    grouped = tool_df.groupby("attr_tool_name").agg(
        call_count=("span_id", "count"),
        success_rate=("attr_success", "mean"),
        p95_ms=("duration_ms", lambda s: s.quantile(0.95)),
    )
    return grouped.reset_index().rename(columns={"attr_tool_name": "tool_name"})


def llm_latency_by_model(df: pd.DataFrame) -> pd.DataFrame:
    """p50 / p95 LLM latency + token usage grouped by model."""
    llm_df = df[df["name"] == "llm.call"].copy()
    if llm_df.empty:
        return pd.DataFrame()
    grouped = llm_df.groupby("attr_model").agg(
        calls=("span_id", "count"),
        p50_ms=("duration_ms", lambda s: s.quantile(0.50)),
        p95_ms=("duration_ms", lambda s: s.quantile(0.95)),
        avg_input_tokens=("attr_input_tokens", "mean"),
        avg_output_tokens=("attr_output_tokens", "mean"),
        avg_retries=("attr_retry_count", "mean"),
    )
    return grouped.reset_index().rename(columns={"attr_model": "model"})


def rag_retrieval_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Retrieved-vs-used document ratio per RAG data source.

    In the current ATLAS implementation ``docs_used_in_context`` equals
    ``doc_ids`` (every retrieved doc is injected into the prompt), so the
    ratio defaults to 1.0. Tracking it as a first-class metric leaves room
    for future reranking/filtering without a schema change.
    """
    rag_df = df[df["name"] == "rag.query"].copy()
    if rag_df.empty:
        return pd.DataFrame()

    def used_count(row) -> int:
        used = row.get("attr_docs_used_in_context")
        if isinstance(used, (list, tuple)):
            return len(used)
        return 0

    def retrieved_count(row) -> int:
        n = row.get("attr_num_results")
        try:
            return int(n) if n is not None else 0
        except (TypeError, ValueError):
            return 0

    rag_df["retrieved"] = rag_df.apply(retrieved_count, axis=1)
    rag_df["used"] = rag_df.apply(used_count, axis=1)

    grouped = rag_df.groupby("attr_data_source").agg(
        queries=("span_id", "count"),
        avg_retrieved=("retrieved", "mean"),
        avg_used=("used", "mean"),
        avg_top_score=("attr_top_score", "mean"),
    )
    grouped["use_ratio"] = grouped["avg_used"] / grouped["avg_retrieved"].replace(0, pd.NA)
    return grouped.reset_index().rename(columns={"attr_data_source": "data_source"})


def retries_per_turn(df: pd.DataFrame) -> pd.DataFrame:
    """Total LLM retry attempts grouped by parent chat.turn."""
    if df.empty:
        return pd.DataFrame()
    turns = df[df["name"] == "chat.turn"][["span_id", "attr_turn_id", "attr_model"]]
    llm = df[df["name"] == "llm.call"][["parent_span_id", "attr_retry_count"]]
    if turns.empty or llm.empty:
        return pd.DataFrame()
    joined = llm.merge(
        turns, left_on="parent_span_id", right_on="span_id", how="inner"
    )
    grouped = joined.groupby("attr_turn_id").agg(
        llm_calls=("attr_retry_count", "count"),
        total_retries=("attr_retry_count", "sum"),
    )
    return grouped.reset_index().rename(columns={"attr_turn_id": "turn_id"})


def main(path: Path) -> None:
    if not path.exists():
        print(f"No spans file at {path}. Run some conversations first.", file=sys.stderr)
        sys.exit(1)

    df = load_spans(path)
    if df.empty:
        print("Spans file is empty.")
        return

    print(f"Loaded {len(df)} spans from {path}\n")
    print(f"Span types: {df['name'].value_counts().to_dict()}\n")

    print("=== Tool success rate ===")
    print(tool_success_rate(df).to_string(index=False))
    print()
    print("=== LLM latency by model ===")
    print(llm_latency_by_model(df).to_string(index=False))
    print()
    print("=== RAG retrieval ratio ===")
    print(rag_retrieval_ratio(df).to_string(index=False))
    print()
    print("=== Retries per turn ===")
    print(retries_per_turn(df).to_string(index=False))


if __name__ == "__main__":
    default_path = Path(__file__).resolve().parents[2] / "logs" / "spans.jsonl"
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path
    main(input_path)
