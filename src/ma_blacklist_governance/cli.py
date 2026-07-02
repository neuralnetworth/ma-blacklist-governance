"""Command-line interface for M&A blacklist governance."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .providers.openai_governance import OfflineGovernanceClient, OpenAIGovernanceClient
from .security import redacted_json
from .workflows import discover, durable_exit_review, load_ticker_file, promotion_review, prune_runs


def _add_common_fixture_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--universe-json", type=Path)
    parser.add_argument("--news-json", type=Path)
    parser.add_argument("--market-json", type=Path)
    parser.add_argument("--rich-evidence-json", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ma-blacklist-governance")
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="check required lite configuration")
    preflight.add_argument("--env-file", type=Path)

    discover_cmd = sub.add_parser("discover", help="run deterministic discovery only")
    _add_common_fixture_args(discover_cmd)
    discover_cmd.add_argument("--durable-blacklist-file", type=Path)

    promotion = sub.add_parser("promotion-review", help="run report-only promotion review")
    _add_common_fixture_args(promotion)
    promotion.add_argument("--candidate", action="append", default=[])
    promotion.add_argument("--candidates-file", type=Path)
    promotion.add_argument("--offline-fake-governance", action="store_true")

    durable = sub.add_parser("durable-exit-review", help="run report-only durable-block exit review")
    _add_common_fixture_args(durable)
    durable.add_argument("--blacklist-file", type=Path, required=True)
    durable.add_argument("--offline-fake-governance", action="store_true")

    prune = sub.add_parser("prune-runs", help="delete old local run artifacts")
    prune.add_argument("--output-root", type=Path, default=Path("runs"))
    prune.add_argument("--older-than-days", type=int, required=True)
    return parser


def _config(args: argparse.Namespace) -> Config:
    return Config.from_env(env_file=getattr(args, "env_file", None), output_root=getattr(args, "output_root", None))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        config = _config(args)
        ok, health = config.preflight()
        print(redacted_json([item.model_dump(mode="json") for item in health], config.secret_values()), end="")
        return 0 if ok else 2

    if args.command == "discover":
        config = _config(args)
        durable_tickers = load_ticker_file(args.durable_blacklist_file)
        result = discover(
            config,
            universe_json=args.universe_json,
            news_json=args.news_json,
            market_json=args.market_json,
            rich_evidence_json=args.rich_evidence_json,
            durable_tickers=durable_tickers,
            output_root=args.output_root,
            run_id=args.run_id,
        )
        print(result.artifacts.report_path)
        return 0

    if args.command == "promotion-review":
        config = _config(args)
        candidates = list(args.candidate or [])
        candidates.extend(load_ticker_file(args.candidates_file))
        client = OfflineGovernanceClient() if args.offline_fake_governance else OpenAIGovernanceClient(config)
        result = promotion_review(
            config,
            governance_client=client,
            operator_candidates=candidates,
            universe_json=args.universe_json,
            news_json=args.news_json,
            market_json=args.market_json,
            rich_evidence_json=args.rich_evidence_json,
            output_root=args.output_root,
            run_id=args.run_id,
        )
        print(result.artifacts.report_path)
        return 0

    if args.command == "durable-exit-review":
        config = _config(args)
        client = OfflineGovernanceClient() if args.offline_fake_governance else OpenAIGovernanceClient(config)
        result = durable_exit_review(
            config,
            blacklist_file=args.blacklist_file,
            governance_client=client,
            universe_json=args.universe_json,
            news_json=args.news_json,
            market_json=args.market_json,
            rich_evidence_json=args.rich_evidence_json,
            output_root=args.output_root,
            run_id=args.run_id,
        )
        print(result.artifacts.report_path)
        return 0

    if args.command == "prune-runs":
        removed = prune_runs(args.output_root, args.older_than_days)
        print(redacted_json({"removed": [str(path) for path in removed]}), end="")
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
