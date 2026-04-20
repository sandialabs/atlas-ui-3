"""Example analysis over ATLAS OpenTelemetry spans.

Run against ``logs/spans.jsonl`` to compute T&E-relevant metrics and save
plots summarizing them:

- Call count + success rate + p95 duration per tool
- Call count per tool (bar chart)
- p50 / p95 LLM latency by model
- RAG retrieval-to-use ratio per data source
- Retries per turn
- Daily time series (turns, tool calls, LLM calls)
- Hour-of-day usage pattern
- Day-of-week usage pattern (weekday vs weekend surfaces here)
- Tool success rate trend over time
- LLM latency trend (p50 + p95) over time

Usage:
    python docs/telemetry/analysis_example.py [SPANS_JSONL] [--output-dir DIR]

Defaults: spans path is ``logs/spans.jsonl`` under the project root; plot
directory is ``<spans_parent>/analysis/``.

Requires: pandas and matplotlib (``uv pip install pandas matplotlib``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # headless — no display needed
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


# Attributes that every aggregation treats as numeric. Coerced at load time
# via ``pd.to_numeric(errors="coerce")`` so a single legacy span with a
# garbage value (e.g. a stringified MagicMock left over from a dev test
# that accidentally wrote to logs/spans.jsonl) doesn't crash the entire
# report. Bad values become NaN; mean/sum/quantile skip NaN automatically.
_NUMERIC_ATTRS = (
    "attr_input_tokens",
    "attr_output_tokens",
    "attr_total_tokens",
    "attr_output_tokens_estimate",
    "attr_retry_count",
    "attr_latency_ms",
    "attr_duration_ms",
    "attr_top_score",
    "attr_num_results",
    "attr_total_documents_searched",
    "attr_query_processing_time_ms",
    "attr_content_size",
    "attr_query_chars",
    "attr_prompt_chars",
    "attr_prompt_tokens",
    "attr_args_size",
    "attr_output_size",
    "attr_temperature",
    "attr_max_tokens",
    "attr_message_count",
    "attr_tool_calls_count",
    "attr_tools_schema_count",
    "attr_chunk_count",
    "attr_output_chars",
    "attr_batch_size",
    "attr_selected_tools_count",
    "attr_selected_prompts_count",
    "attr_selected_data_sources_count",
)


def load_spans(path: Path) -> pd.DataFrame:
    """Load one-line-per-span JSONL into a flat DataFrame.

    Span attributes are promoted into top-level columns prefixed with ``attr_``.
    ``duration_ms`` and ``start_time`` (datetime64) are derived for convenience.
    Known-numeric attributes are coerced to numeric; unparseable values
    (legacy dev data, MagicMock leftovers) become NaN rather than crashing
    aggregations downstream.
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
    if "start_time_ns" in df.columns:
        df["start_time"] = pd.to_datetime(df["start_time_ns"], unit="ns", utc=True)

    # Defensive numeric coercion — see note on _NUMERIC_ATTRS above.
    for col in _NUMERIC_ATTRS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


def tool_success_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Count calls, success rate, and p95 duration per tool.

    Resilient to missing ``attr_success``: datasets containing only very
    old tool.call spans that predate the attribute still get call_count
    and p95_ms rows.
    """
    tool_df = df[df["name"] == "tool.call"].copy()
    if tool_df.empty or "attr_tool_name" not in tool_df.columns:
        return pd.DataFrame(columns=["tool_name", "call_count", "success_rate", "p95_ms"])
    agg_spec: Dict[str, Any] = {
        "call_count": ("span_id", "count"),
        "p95_ms": ("duration_ms", lambda s: s.quantile(0.95)),
    }
    if "attr_success" in tool_df.columns:
        agg_spec["success_rate"] = ("attr_success", "mean")
    grouped = tool_df.groupby("attr_tool_name").agg(**agg_spec)
    return (
        grouped.reset_index()
        .rename(columns={"attr_tool_name": "tool_name"})
        .sort_values("call_count", ascending=False)
    )


def tool_call_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Pure call-count-per-tool — one row per tool, sorted descending."""
    tool_df = df[df["name"] == "tool.call"]
    if tool_df.empty:
        return pd.DataFrame(columns=["tool_name", "call_count"])
    counts = (
        tool_df.groupby("attr_tool_name")
        .size()
        .rename("call_count")
        .reset_index()
        .rename(columns={"attr_tool_name": "tool_name"})
        .sort_values("call_count", ascending=False)
    )
    return counts


def llm_latency_by_model(df: pd.DataFrame) -> pd.DataFrame:
    """p50 / p95 LLM latency + token usage grouped by model.

    Resilient to missing columns: ``attr_input_tokens`` / ``attr_output_tokens``
    only appear on non-streaming ``llm.call`` spans (streaming calls don't
    carry litellm usage metadata and publish ``attr_output_tokens_estimate``
    under a distinct name). The agg dict is built dynamically so a dataset
    with only streaming spans still produces latency + retry stats.
    """
    llm_df = df[df["name"] == "llm.call"].copy()
    if llm_df.empty or "attr_model" not in llm_df.columns:
        return pd.DataFrame()
    agg_spec: Dict[str, Any] = {
        "calls": ("span_id", "count"),
        "p50_ms": ("duration_ms", lambda s: s.quantile(0.50)),
        "p95_ms": ("duration_ms", lambda s: s.quantile(0.95)),
    }
    if "attr_input_tokens" in llm_df.columns:
        agg_spec["avg_input_tokens"] = ("attr_input_tokens", "mean")
    if "attr_output_tokens" in llm_df.columns:
        agg_spec["avg_output_tokens"] = ("attr_output_tokens", "mean")
    if "attr_output_tokens_estimate" in llm_df.columns:
        agg_spec["avg_output_tokens_estimate"] = ("attr_output_tokens_estimate", "mean")
    if "attr_retry_count" in llm_df.columns:
        agg_spec["avg_retries"] = ("attr_retry_count", "mean")
    grouped = llm_df.groupby("attr_model").agg(**agg_spec)
    return grouped.reset_index().rename(columns={"attr_model": "model"})


def rag_retrieval_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Retrieved-vs-used document ratio per RAG data source."""
    rag_df = df[df["name"] == "rag.query"].copy()
    if rag_df.empty or "attr_data_source" not in rag_df.columns:
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

    rag_agg: Dict[str, Any] = {
        "queries": ("span_id", "count"),
        "avg_retrieved": ("retrieved", "mean"),
        "avg_used": ("used", "mean"),
    }
    if "attr_top_score" in rag_df.columns:
        rag_agg["avg_top_score"] = ("attr_top_score", "mean")
    grouped = rag_df.groupby("attr_data_source").agg(**rag_agg)
    grouped["use_ratio"] = grouped["avg_used"] / grouped["avg_retrieved"].replace(0, pd.NA)
    return grouped.reset_index().rename(columns={"attr_data_source": "data_source"})


def retries_per_turn(df: pd.DataFrame) -> pd.DataFrame:
    """Total LLM retry attempts grouped by parent chat.turn."""
    if df.empty:
        return pd.DataFrame()
    turns = df[df["name"] == "chat.turn"].reindex(
        columns=["span_id", "attr_turn_id", "attr_model"]
    )
    llm = df[df["name"] == "llm.call"].reindex(
        columns=["parent_span_id", "attr_retry_count"]
    )
    if turns.empty or llm.empty:
        return pd.DataFrame()
    if turns["attr_turn_id"].isna().all() or llm["attr_retry_count"].isna().all():
        return pd.DataFrame()
    joined = llm.merge(
        turns, left_on="parent_span_id", right_on="span_id", how="inner"
    )
    if joined.empty or joined["attr_turn_id"].isna().all():
        return pd.DataFrame()
    grouped = joined.groupby("attr_turn_id").agg(
        llm_calls=("attr_retry_count", "count"),
        total_retries=("attr_retry_count", "sum"),
    )
    return grouped.reset_index().rename(columns={"attr_turn_id": "turn_id"})


# ---------------------------------------------------------------------------
# Time-series aggregations
# ---------------------------------------------------------------------------


def daily_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Daily counts of chat.turn / tool.call / llm.call / rag.query spans.

    Useful for spotting trends and weekend-vs-weekday patterns alongside
    ``day_of_week_counts`` and ``hourly_counts``.
    """
    if df.empty or "start_time" not in df.columns:
        return pd.DataFrame()
    sub = df[df["name"].isin(["chat.turn", "tool.call", "llm.call", "rag.query"])].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["date"] = sub["start_time"].dt.tz_convert(None).dt.floor("D")
    pivot = (
        sub.groupby(["date", "name"]).size().unstack(fill_value=0).sort_index()
    )
    for col in ("chat.turn", "tool.call", "llm.call", "rag.query"):
        if col not in pivot.columns:
            pivot[col] = 0
    return pivot[["chat.turn", "tool.call", "llm.call", "rag.query"]]


def day_of_week_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Average daily counts per day-of-week.

    Answers "is weekend usage different from weekday usage?" by normalizing
    across however many Mondays/Tuesdays/... the data happens to cover.
    """
    daily = daily_counts(df)
    if daily.empty:
        return pd.DataFrame()
    daily = daily.copy()
    daily["dow"] = daily.index.dayofweek  # 0=Mon..6=Sun
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    grouped = daily.groupby("dow").mean()
    grouped.index = [dow_labels[i] for i in grouped.index]
    return grouped


def hourly_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Counts grouped by hour-of-day (UTC). Good for surfacing working-hours
    vs overnight patterns.
    """
    if df.empty or "start_time" not in df.columns:
        return pd.DataFrame()
    sub = df[df["name"].isin(["chat.turn", "tool.call", "llm.call", "rag.query"])].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["hour"] = sub["start_time"].dt.hour
    pivot = sub.groupby(["hour", "name"]).size().unstack(fill_value=0)
    for col in ("chat.turn", "tool.call", "llm.call", "rag.query"):
        if col not in pivot.columns:
            pivot[col] = 0
    return pivot[["chat.turn", "tool.call", "llm.call", "rag.query"]].sort_index()


def llm_latency_over_time(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Resampled p50 / p95 LLM latency (ms) at ``freq`` frequency."""
    llm_df = df[df["name"] == "llm.call"].copy()
    if llm_df.empty or "start_time" not in llm_df.columns:
        return pd.DataFrame()
    llm_df = llm_df.dropna(subset=["duration_ms"])
    if llm_df.empty:
        return pd.DataFrame()
    llm_df = llm_df.set_index(llm_df["start_time"].dt.tz_convert(None))
    resampled = llm_df["duration_ms"].resample(freq).agg(
        p50_ms=lambda s: s.quantile(0.50) if len(s) else None,
        p95_ms=lambda s: s.quantile(0.95) if len(s) else None,
        calls="count",
    )
    return resampled.dropna(how="all")


def tool_success_over_time(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Resampled tool success rate and call count at ``freq`` frequency."""
    tool_df = df[df["name"] == "tool.call"].copy()
    if (
        tool_df.empty
        or "start_time" not in tool_df.columns
        or "attr_success" not in tool_df.columns
    ):
        return pd.DataFrame()
    tool_df = tool_df.set_index(tool_df["start_time"].dt.tz_convert(None))
    tool_df["success_num"] = tool_df["attr_success"].astype(float)
    resampled = tool_df["success_num"].resample(freq).agg(
        success_rate="mean",
        call_count="count",
    )
    return resampled.dropna(how="all")


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_tool_call_counts(counts: pd.DataFrame, out: Path) -> Optional[Path]:
    if counts.empty:
        return None
    fig, ax = plt.subplots(figsize=(10, max(3, 0.35 * len(counts))))
    ax.barh(counts["tool_name"], counts["call_count"], color="#4C72B0")
    ax.invert_yaxis()  # most-called at top
    ax.set_xlabel("Call count")
    ax.set_title("Tool call counts")
    for y, v in enumerate(counts["call_count"]):
        ax.text(v, y, f" {v}", va="center", fontsize=8)
    return _save(fig, out)


def plot_daily_counts(daily: pd.DataFrame, out: Path) -> Optional[Path]:
    if daily.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 4))
    for col in daily.columns:
        ax.plot(daily.index, daily[col], marker="o", label=col)
    ax.set_xlabel("Date")
    ax.set_ylabel("Span count")
    ax.set_title("Daily span counts by type")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    return _save(fig, out)


def plot_day_of_week(dow: pd.DataFrame, out: Path) -> Optional[Path]:
    if dow.empty:
        return None
    fig, ax = plt.subplots(figsize=(9, 4))
    dow.plot(kind="bar", ax=ax, width=0.8)
    ax.set_xlabel("Day of week")
    ax.set_ylabel("Average daily span count")
    ax.set_title("Average usage by day of week (weekend dip shows here)")
    ax.legend(loc="best")
    ax.grid(axis="y", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=0)
    return _save(fig, out)


def plot_hourly(hourly: pd.DataFrame, out: Path) -> Optional[Path]:
    if hourly.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 4))
    for col in hourly.columns:
        ax.plot(hourly.index, hourly[col], marker="o", label=col)
    ax.set_xlabel("Hour of day (UTC)")
    ax.set_ylabel("Span count")
    ax.set_title("Hour-of-day usage pattern (UTC)")
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, out)


def plot_llm_latency_trend(trend: pd.DataFrame, out: Path) -> Optional[Path]:
    if trend.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 4))
    if "p50_ms" in trend.columns:
        ax.plot(trend.index, trend["p50_ms"], marker="o", label="p50", color="#4C72B0")
    if "p95_ms" in trend.columns:
        ax.plot(trend.index, trend["p95_ms"], marker="o", label="p95", color="#DD8452")
    ax.set_xlabel("Date")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("LLM call latency over time (p50 / p95)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    return _save(fig, out)


def plot_tool_success_trend(trend: pd.DataFrame, out: Path) -> Optional[Path]:
    if trend.empty:
        return None
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    ax1.plot(trend.index, trend["success_rate"], marker="o", color="#55A868")
    ax1.set_ylabel("Success rate")
    ax1.set_ylim(0, 1.05)
    ax1.set_title("Tool success rate and call volume over time")
    ax1.grid(alpha=0.3)
    ax2.bar(trend.index, trend["call_count"], color="#4C72B0", width=0.8)
    ax2.set_xlabel("Date")
    ax2.set_ylabel("Call count")
    ax2.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate()
    return _save(fig, out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print(title: str, frame: pd.DataFrame) -> None:
    print(f"=== {title} ===")
    if frame is None or frame.empty:
        print("(no data)\n")
        return
    print(frame.to_string())
    print()


def main(path: Path, output_dir: Path) -> None:
    if not path.exists():
        print(f"No spans file at {path}. Run some conversations first.", file=sys.stderr)
        sys.exit(1)

    df = load_spans(path)
    if df.empty:
        print("Spans file is empty.")
        return

    print(f"Loaded {len(df)} spans from {path}")
    print(f"Span types: {df['name'].value_counts().to_dict()}\n")

    _print("Tool success rate", tool_success_rate(df))
    counts = tool_call_counts(df)
    _print("Tool call counts", counts)
    _print("LLM latency by model", llm_latency_by_model(df))
    _print("RAG retrieval ratio", rag_retrieval_ratio(df))
    _print("Retries per turn", retries_per_turn(df))

    daily = daily_counts(df)
    dow = day_of_week_counts(df)
    hourly = hourly_counts(df)
    llm_trend = llm_latency_over_time(df)
    tool_trend = tool_success_over_time(df)

    _print("Daily counts", daily)
    _print("Day-of-week counts", dow)
    _print("LLM latency over time", llm_trend)
    _print("Tool success over time", tool_trend)

    saved = [
        plot_tool_call_counts(counts, output_dir / "tool_call_counts.png"),
        plot_daily_counts(daily, output_dir / "daily_counts.png"),
        plot_day_of_week(dow, output_dir / "day_of_week.png"),
        plot_hourly(hourly, output_dir / "hourly_pattern.png"),
        plot_llm_latency_trend(llm_trend, output_dir / "llm_latency_trend.png"),
        plot_tool_success_trend(tool_trend, output_dir / "tool_success_trend.png"),
    ]
    saved_paths = [str(p) for p in saved if p is not None]
    if saved_paths:
        print("Saved plots:")
        for p in saved_paths:
            print(f"  {p}")
    else:
        print("No plots saved (insufficient data).")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "spans_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to spans.jsonl (default: <project_root>/logs/spans.jsonl)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to save plots (default: <spans_parent>/analysis)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    default_spans = Path(__file__).resolve().parents[2] / "logs" / "spans.jsonl"
    spans_path = args.spans_path or default_spans
    out_dir = args.output_dir or (spans_path.parent / "analysis")
    main(spans_path, out_dir)
