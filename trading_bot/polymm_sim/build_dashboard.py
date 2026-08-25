"""
Inject report.json into dashboard_template.html to produce a self-contained
results page. Run run_report.py first to (re)generate report.json.

    python3 polymm_sim/run_report.py --n-seeds 200 --n-matches 1500 --out polymm_sim/report.json
    python3 polymm_sim/build_dashboard.py
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default=str(HERE / "report.json"))
    p.add_argument("--template", default=str(HERE / "dashboard_template.html"))
    p.add_argument("--out", default=str(HERE / "dashboard.html"))
    args = p.parse_args()

    report = json.loads(Path(args.report).read_text())
    tmpl = Path(args.template).read_text()
    out = tmpl.replace("%%REPORT_JSON%%", json.dumps(report))
    Path(args.out).write_text(out)
    print(f"wrote {args.out} ({len(out):,} bytes)")


if __name__ == "__main__":
    main()
