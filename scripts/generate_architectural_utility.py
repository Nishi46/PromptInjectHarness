"""S5-02 -- D7 (`dual_llm`) utility-tax measurement: benign-task completion
rate for `dual_llm` vs. `no_defense`, per suite and model, computed from the
same trace DBs `configs/architectural_static_sweep.yaml`/
`configs/architectural_mcp_sweep.yaml` write into (those configs' own header
comments explain the `no_defense`-rows-for-free reuse). Reuses
`scoring.utility.benign_utility_rate` unmodified -- it already groups by
(defense, model) over whichever defenses are present in a trace DB, so
`dual_llm` and `no_defense` rows coexist there without any query change;
`scoring.utility.utility_tax_table` (this sprint's small addition to that
module) pairs those two rows per model into the explicit tax number the
sprint goal names.

Usage: .venv/bin/python scripts/generate_architectural_utility.py
"""

from __future__ import annotations

from pathlib import Path

from injection_pareto.scoring import UtilityTaxRow, benign_utility_rate, utility_tax_table
from injection_pareto.trace import connect

_STATIC_TRACE_DB = Path("runs/local/static_sweep/trace.db")
_MCP_TRACE_DB = Path("runs/local/mcp_sweep/trace.db")

_BASELINE_DEFENSE = "no_defense"
_TARGET_DEFENSE = "dual_llm"


def _render(rows: list[tuple[str, UtilityTaxRow]]) -> str:
    lines = [
        f"# D7 (`{_TARGET_DEFENSE}`) utility tax vs. `{_BASELINE_DEFENSE}`",
        "",
        "| Suite | Model | no_defense rate (n) | dual_llm rate (n) | Tax (no_defense - dual_llm) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for suite, row in rows:
        base = (
            f"{row.baseline_rate:.3f} ({row.baseline_n})"
            if row.baseline_rate is not None
            else "n/a"
        )
        tgt = (
            f"{row.target_rate:.3f} ({row.target_n})" if row.target_rate is not None else "n/a"
        )
        tax = f"{row.tax:+.3f}" if row.tax is not None else "n/a"
        lines.append(f"| {suite} | {row.model} | {base} | {tgt} | {tax} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    static_conn = connect(_STATIC_TRACE_DB)
    mcp_conn = connect(_MCP_TRACE_DB)
    try:
        static_rows = utility_tax_table(
            benign_utility_rate(static_conn), baseline=_BASELINE_DEFENSE, target=_TARGET_DEFENSE
        )
        mcp_rows = utility_tax_table(
            benign_utility_rate(mcp_conn), baseline=_BASELINE_DEFENSE, target=_TARGET_DEFENSE
        )
    finally:
        static_conn.close()
        mcp_conn.close()

    rows = [("workspace", r) for r in static_rows] + [("mcp", r) for r in mcp_rows]
    print(_render(rows))


if __name__ == "__main__":
    main()
