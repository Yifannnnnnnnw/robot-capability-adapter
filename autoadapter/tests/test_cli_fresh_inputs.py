from __future__ import annotations

from autoadapter2.__main__ import _parser


def test_full_cli_does_not_offer_reference_skip_or_sealed_reuse() -> None:
    help_text = _parser().format_help()
    full_help = _parser()._subparsers._group_actions[0].choices["full"].format_help()

    assert "fresh configured cells" in help_text
    assert "--skip-reference-calibration" not in full_help
    assert "--reuse-sealed-inputs-from" not in full_help
