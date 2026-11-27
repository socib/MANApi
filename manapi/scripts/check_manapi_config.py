#!/usr/bin/python3
"""Read-only MANApi configuration check.

No camera capture, cleanup or implicit email is performed.
"""

import argparse
import contextlib
import importlib
import io
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pythonvideoFunctions as functions


def fail_config(path, *, reason="Missing info.txt"):
    """Replace noInfo() during validation so no email/log side effects occur."""
    raise functions.ManapiConfigError(reason)


def load_values(path):
    """Load and validate the MANApi configuration interface."""

    # noInfo normally logs and may send email.
    # Never do that from a read-only configuration check.
    with patch.object(functions, "noInfo", fail_config):
        with contextlib.redirect_stdout(io.StringIO()):
            values = functions.readInfo(path)

    if len(values) != 19:
        raise RuntimeError(
            f"Invalid configuration interface: expected 19 values, "
            f"got {len(values)}."
        )

    # Values 0-16 must remain strings.
    if not all(isinstance(v, str) for v in values[:17]):
        raise RuntimeError(
            "Invalid configuration interface: values 0-16 must be strings."
        )

    # Value 17: parsed timestack transect.
    transect = values[17]

    if not (
        isinstance(transect, tuple)
        and len(transect) == 2
        and all(
            isinstance(point, tuple)
            and len(point) == 2
            and all(isinstance(coord, int) for coord in point)
            for point in transect
        )
    ):
        raise RuntimeError(
            "Invalid TimestackTransect returned by configuration."
        )

    # Value 18: timestack orientation.
    if values[18] not in ("N", "E", "S", "W"):
        raise RuntimeError(
            "Invalid TimestackOrientation returned by configuration."
        )

    return values


def runtime_checks(values):
    """Check packages, commands and writable runtime directories."""

    if sys.prefix != sys.base_prefix:
        raise functions.ManapiConfigError(
            "Use system Python, not a virtual environment."
        )

    # Required Python packages.
    for name in (
        "dotenv",
        "numpy",
        "cv2",
        "numexpr",
        "pexpect",
        "psutil",
        "matplotlib",
    ):
        try:
            importlib.import_module(name)
        except ImportError:
            raise functions.ManapiConfigError(
                "Missing system Python dependency: " + name
            ) from None

    # Required system commands.
    for command in (
        "rpicam-hello",
        "rpicam-vid",
        "vcgencmd",
        "rsync",
        "ssh",
        "curl",
        "tail",
        "toilet",
    ):
        if shutil.which(command) is None:
            raise functions.ManapiConfigError(
                "Missing runtime command: " + command
            )

    # Check shutdown command availability
    has_shutdown_command = any(
        shutil.which(cmd) is not None
        or Path(f"/sbin/{cmd}").exists()
        or Path(f"/usr/sbin/{cmd}").exists()
        for cmd in ("shutdown", "poweroff", "systemctl")
    )
    if not has_shutdown_command:
        raise functions.ManapiConfigError(
            "Missing system shutdown command: neither shutdown, poweroff, nor systemctl found."
        )

    # Check passwordless sudo permissions for emergency poweroff on extreme temperature
    if os.geteuid() != 0:
        shutdown_permitted = False
        for cmd in (
            ["sudo", "-n", "/sbin/shutdown", "--help"],
            ["sudo", "-n", "/sbin/poweroff", "--help"],
            ["sudo", "-n", "shutdown", "--help"],
            ["sudo", "-n", "poweroff", "--help"],
        ):
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                if result.returncode == 0:
                    shutdown_permitted = True
                    break
            except Exception:
                continue

        if not shutdown_permitted:
            raise functions.ManapiConfigError(
                "Missing passwordless sudo permission for shutdown/poweroff. "
                "Ensure /etc/sudoers.d/ contains NOPASSWD for /sbin/shutdown and /sbin/poweroff."
            )

    # Required local directories.
    for name, folder in (
        ("DirLocal", Path(values[2])),
        ("DirDest", Path(values[3])),
        ("local logs", Path(values[2]) / "scripts" / "logs"),
        ("local images", Path(values[2]) / "images"),
        ("local videos", Path(values[2]) / "videos"),
    ):
        if not folder.is_dir() or not os.access(
            folder, os.W_OK | os.X_OK
        ):
            raise functions.ManapiConfigError(
                "Missing/not writable runtime directory: " + name
            )

    # DirDest should correspond to DirLocal/videos.
    if Path(values[3]).resolve() != (
        Path(values[2]) / "videos"
    ).resolve():
        raise functions.ManapiConfigError(
            "DirDest differs from DirLocal/videos; "
            "review recording/processing paths."
        )

    # Numeric configuration values.
    for name, val, integer in (
        ("SshPort", values[6], True),
        ("VideoDuration", values[13], True),
        ("VideoFps", values[14], False),
        ("ImageWidth", values[15], True),
        ("ImageHeight", values[16], True),
    ):
        try:
            num = int(val) if integer else float(val)

            if (
                num <= 0
                or not math.isfinite(num)
                or (name == "SshPort" and num > 65535)
            ):
                raise ValueError

        except (ValueError, TypeError):
            raise functions.ManapiConfigError(
                "Invalid positive numeric setting: " + name
            ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--info",
        type=Path,
        default=Path.home() / ".config/manapi/info.txt",
        help="Path to the MANApi info.txt configuration file.",
    )

    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Also check packages, commands and local directories.",
    )

    parser.add_argument(
        "--send-test-email",
        action="store_true",
        help="Explicitly send one email after successful checks.",
    )

    args = parser.parse_args()

    try:
        values = load_values(args.info)

        if args.runtime:
            runtime_checks(values)

        print(
            f"Configuration loaded successfully ({len(values)} values). "
            "Password not displayed."
        )

        if args.send_test_email:
            accepted = functions.toolSendMail(
                values[8],
                values[9],
                values[10],
                "[" + values[1] + " " + values[11]
                + "] MANApi configuration test",
                "Configuration test for station " + values[0],
                lookup_wan_ip=False,
            )

            if not accepted:
                raise RuntimeError("SMTP did not accept all recipients")
            print(
                "SMTP accepted the message; "
                "verify delivery in the recipient inbox."
            )

        else:
            print(
                "No email, camera capture, cleanup or deployment performed."
            )

        return 0

    except functions.ManapiConfigError as exc:
        print(
            "Configuration check failed: " + str(exc),
            file=sys.stderr,
        )

    except Exception as exc:
        print(
            "Check failed ("
            + type(exc).__name__
            + "); no setting values printed.",
            file=sys.stderr,
        )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())