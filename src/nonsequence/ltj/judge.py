"""CLI for LTJ label evaluation."""

try:
    from ._judge_core import cli
except ImportError:
    from _judge_core import cli


def main() -> None:
    """Run label evaluation without reason extraction."""
    cli(include_reason=False)


if __name__ == "__main__":
    main()
