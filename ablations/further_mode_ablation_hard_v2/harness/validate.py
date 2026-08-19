"""Print a machine-readable command-local registration validation record."""

from __future__ import annotations

import json

from arm_registry import arm_metadata, validate_command_local_registration


def main() -> None:
    print(
        json.dumps(
            {
                "registration": validate_command_local_registration(),
                "metadata": arm_metadata(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
