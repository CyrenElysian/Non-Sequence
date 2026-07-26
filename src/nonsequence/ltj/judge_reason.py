"""CLI for LTJ label evaluation with reason extraction."""

try:
    from ._judge_core import cli
except ImportError:
    from _judge_core import cli


def main() -> None:
    """Run label evaluation and retain model reasons."""
    cli(include_reason=True)


if __name__ == "__main__":
    main()
