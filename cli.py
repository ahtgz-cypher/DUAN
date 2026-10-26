"""Command-line runner for offline, synthetic-only experiments."""
import argparse
import json
import sys
from pathlib import Path

from lab import SimulationLab


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Chạy mô phỏng tương tác ngoại tuyến bằng dữ liệu giả.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Tạo và chạy một thử nghiệm mô phỏng")
    run.add_argument("--name", default="Thử nghiệm CLI")
    run.add_argument("--seed", type=int, default=2026)
    run.add_argument("--accounts", type=int, default=12)
    run.add_argument("--creators", type=int, default=3)
    run.add_argument("--videos-per-creator", type=int, default=3)
    run.add_argument("--views-per-account", type=int, default=2)
    run.add_argument("--like-probability", type=float, default=.35)
    run.add_argument("--follow-probability", type=float, default=.2)
    run.add_argument("--data-dir", default="data")
    run.add_argument("--output", default="", help="Ghi báo cáo JSON vào đường dẫn này")
    args = parser.parse_args()
    lab = SimulationLab(Path(args.data_dir))
    run_info = lab.create_run({
        "name": args.name, "seed": args.seed, "account_count": args.accounts,
        "creator_count": args.creators, "videos_per_creator": args.videos_per_creator,
        "views_per_account": args.views_per_account, "like_probability": args.like_probability,
        "follow_probability": args.follow_probability, "delay_ms": 0,
    })
    while lab.tick(run_info["id"]):
        if lab.summary(run_info["id"])["status"] == "completed":
            break
    report = lab.report(run_info["id"])
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized + "\n", encoding="utf-8")
        print(f"Đã ghi báo cáo mô phỏng: {target.resolve()}")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
