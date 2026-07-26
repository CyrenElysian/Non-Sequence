"""Inspect and correct constructed script graphs with an LLM."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Final

from nonsequence.common.batch_pipeline import run_batch_pipeline

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTERIM_DIR: Final = PROJECT_ROOT / "data" / "interim" / "ctrlscript"
DEFAULT_PROMPT: Final = PROJECT_ROOT / "prompts" / "construction" / "check_prompt.txt"
DEFAULT_INPUT: Final = INTERIM_DIR / "processed_data.json"
DEFAULT_OUTPUT: Final = PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript.json"
DEFAULT_LOG: Final = INTERIM_DIR / "change_log.json"
DEFAULT_CHECKPOINT: Final = INTERIM_DIR / "checkpoint_check.json"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--change-log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=10)
    parser.add_argument("--batch-delay", type=float, default=2)
    parser.add_argument("--overwrite-checkpoint", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Run the graph inspection pipeline."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        run_batch_pipeline(
            input_path=args.input,
            prompt_path=args.prompt,
            output_path=args.output,
            log_path=args.change_log,
            checkpoint_path=args.checkpoint,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            model=args.model,
            batch_size=args.batch_size,
            retries=args.retries,
            retry_delay=args.retry_delay,
            batch_delay=args.batch_delay,
            overwrite_checkpoint=args.overwrite_checkpoint,
            user_instruction=(
                "Inspect and correct every item strictly according to the rules."
            ),
            reasoning=False,
        )
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
