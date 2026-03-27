#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import datetime as dt
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

APP_ID = "ai_dashboard"
DEFAULT_OUTPUT = Path("bridge/out/usage.txt")
DEFAULT_MANUAL = Path("bridge/providers.local.json")
PREFERRED_ORDER = {
    "chatgpt": 0,
    "claude": 1,
    "codex": 2,
    "cursor": 3,
    "gemini": 4,
}
DEFAULT_ICONS = {
    "chatgpt": "GP",
    "claude": "CL",
    "codex": "<>",
    "cursor": "CU",
    "gemini": "GM",
}
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PERCENT_LEFT_RE = re.compile(r"([0-9]{1,3})%\s+left", re.IGNORECASE)
BLE_SERIAL_TX_UUID = "19ed82ae-ed21-4c9d-4145-228e62fe0000"
BLE_END_MARKER = "\n<<<END>>>\n"
BLE_CHUNK_SIZE = 180
USB_DESTINATION = f"/ext/apps_data/{APP_ID}/usage.txt"


@dataclass(slots=True)
class ProviderSnapshot:
    id: str
    name: str
    icon: str
    used: int
    window: str
    reset: str
    detail: str
    source: str

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ProviderSnapshot":
        provider_id = str(raw["id"]).strip().lower()
        if not provider_id:
            raise ValueError("provider id is required")

        name = str(raw.get("name") or provider_id.title()).strip()
        icon = str(raw.get("icon") or DEFAULT_ICONS.get(provider_id, provider_id[:2].upper())).strip()

        used_value = raw.get("used")
        remaining_value = raw.get("remaining")
        if used_value is None and remaining_value is None:
            raise ValueError(f"{provider_id}: either 'used' or 'remaining' is required")

        if used_value is None:
            used = 100 - int(round(float(remaining_value)))
        else:
            used = int(round(float(used_value)))

        return cls(
            id=provider_id,
            name=name,
            icon=icon,
            used=max(0, min(100, used)),
            window=str(raw.get("window") or "Usage window").strip(),
            reset=str(raw.get("reset") or "Unknown reset").strip(),
            detail=str(raw.get("detail") or "").strip(),
            source=str(raw.get("source") or "manual").strip(),
        )

    def to_line(self) -> str:
        fields = [
            "provider",
            self.id,
            self.name,
            self.icon,
            str(self.used),
            self.window,
            self.reset,
            self.detail,
            self.source,
        ]
        return "|".join(_sanitize_field(field) for field in fields)


class CodexCollector:
    command = "codex"

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def collect(self) -> list[ProviderSnapshot]:
        result = self._read_rate_limits()
        rate_limits = result.get("rateLimits") or {}
        rate_limits_by_limit = result.get("rateLimitsByLimitId") or {}
        if isinstance(rate_limits_by_limit, dict):
            rate_limits = rate_limits_by_limit.get("codex") or rate_limits

        primary = rate_limits.get("primary")
        secondary = rate_limits.get("secondary")
        plan_type = str(rate_limits.get("planType") or "").strip()

        if not primary and not secondary and plan_type.lower() == "free":
            return [
                ProviderSnapshot(
                    id="codex",
                    name="Codex",
                    icon="<>",
                    used=0,
                    window="Free plan",
                    reset="No active rate limit yet",
                    detail="Make a request to populate usage buckets",
                    source="codex-rpc",
                )
            ]

        windows: list[tuple[str, dict[str, Any]]] = []
        if isinstance(primary, dict):
            windows.append(("5h limit", primary))
        if isinstance(secondary, dict):
            windows.append(("Weekly", secondary))
        if not windows:
            return []

        chosen_label, chosen_window = max(
            windows, key=lambda item: float(item[1].get("usedPercent") or 0.0)
        )
        used = int(round(float(chosen_window.get("usedPercent") or 0.0)))
        reset = _format_reset_epoch(chosen_window.get("resetsAt"))

        detail_parts: list[str] = []
        for label, window in windows:
            if window is chosen_window:
                continue
            remaining = 100 - int(round(float(window.get("usedPercent") or 0.0)))
            detail_parts.append(f"{label} {remaining}% left")
        if plan_type:
            detail_parts.append(plan_type.replace("_", " ").title())

        return [
            ProviderSnapshot(
                id="codex",
                name="Codex",
                icon="<>",
                used=used,
                window=chosen_label,
                reset=reset,
                detail=" / ".join(detail_parts) or "Collected through codex app-server",
                source="codex-rpc",
            )
        ]

    def _read_rate_limits(self) -> dict[str, Any]:
        process = subprocess.Popen(
            [self.command, "-s", "read-only", "-a", "untrusted", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        try:
            self._send(process, {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "flipper-ai-dashboard", "version": "0.1.0"}}})
            self._await_response(process, 1)
            self._send(process, {"method": "initialized", "params": {}})
            self._send(process, {"id": 2, "method": "account/rateLimits/read", "params": {}})
            response = self._await_response(process, 2)
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("codex app-server returned no rate limits result")
            return result
        finally:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    def _send(self, process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def _await_response(self, process: subprocess.Popen[str], request_id: int, timeout: float = 10.0) -> dict[str, Any]:
        assert process.stdout is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                break

            line = process.stdout.readline()
            if not line:
                break

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            if message.get("id") != request_id:
                continue
            if isinstance(message.get("error"), dict):
                raise RuntimeError(message["error"].get("message", "codex app-server returned an error"))
            return message

        raise RuntimeError("timed out waiting for codex app-server")


class ClaudeCollector:
    command = "claude"

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def collect(self) -> list[ProviderSnapshot]:
        completed = subprocess.run(
            [self.command, "/usage", "--allowed-tools", ""],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
            check=False,
        )
        text = ANSI_RE.sub("", completed.stdout or completed.stderr or "")
        if not text.strip():
            return []

        session = self._extract_window(text, "5h limit", ("5h", "session"))
        weekly = self._extract_window(text, "Weekly", ("weekly", "week"))
        windows = [window for window in (session, weekly) if window]
        if not windows:
            return []

        chosen = max(windows, key=lambda item: item["used"])
        detail_parts = [part["summary"] for part in windows if part is not chosen]

        return [
            ProviderSnapshot(
                id="claude",
                name="Claude",
                icon="CL",
                used=chosen["used"],
                window=chosen["label"],
                reset=chosen["reset"],
                detail=" / ".join(detail_parts) or "Parsed from claude /usage",
                source="claude-cli",
            )
        ]

    def _extract_window(self, text: str, label: str, tokens: tuple[str, ...]) -> dict[str, Any] | None:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            lower = line.lower()
            if not any(token in lower for token in tokens):
                continue

            window_lines = lines[index : index + 12]
            percent_left: int | None = None
            reset = "Unknown reset"
            for candidate in window_lines:
                match = PERCENT_LEFT_RE.search(candidate)
                if match and percent_left is None:
                    percent_left = int(match.group(1))
                if "reset" in candidate.lower():
                    reset = candidate.strip()

            if percent_left is None:
                continue

            used = 100 - percent_left
            return {
                "label": label,
                "used": max(0, min(100, used)),
                "reset": reset,
                "summary": f"{label} {percent_left}% left",
            }
        return None


def _sanitize_field(value: str) -> str:
    cleaned = str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:63]


def _format_reset_epoch(value: Any) -> str:
    if value in (None, "", 0):
        return "Reset unknown"
    try:
        target = dt.datetime.fromtimestamp(int(value), tz=dt.timezone.utc).astimezone()
    except (TypeError, ValueError, OSError):
        return "Reset unknown"

    delta = target - dt.datetime.now(dt.timezone.utc).astimezone()
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes <= 0:
        return "Resets soon"

    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days > 0:
        return f"Resets in {days}d {hours}h"
    if hours > 0:
        return f"Resets in {hours}h {minutes}m"
    return f"Resets in {minutes}m"


def _load_manual_providers(path: Path) -> list[ProviderSnapshot]:
    if not path.exists():
        return []

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw_providers = raw.get("providers", [])
    else:
        raw_providers = raw
    if not isinstance(raw_providers, list):
        raise ValueError("manual provider file must contain a list or a {\"providers\": [...]} object")

    providers: list[ProviderSnapshot] = []
    for entry in raw_providers:
        if not isinstance(entry, dict):
            raise ValueError("manual provider entries must be objects")
        providers.append(ProviderSnapshot.from_mapping(entry))
    return providers


def _sample_providers() -> list[ProviderSnapshot]:
    return [
        ProviderSnapshot(
            id="chatgpt",
            name="ChatGPT",
            icon="GP",
            used=32,
            window="GPT-5 Plus",
            reset="Renews next week",
            detail="Sample overview provider",
            source="sample",
        ),
        ProviderSnapshot(
            id="claude",
            name="Claude",
            icon="CL",
            used=61,
            window="5h limit",
            reset="Resets in 1h 12m",
            detail="Weekly 82% left / Max plan",
            source="sample",
        ),
        ProviderSnapshot(
            id="codex",
            name="Codex",
            icon="<>",
            used=44,
            window="5h limit",
            reset="Resets in 2h 41m",
            detail="Weekly 73% left / Plus",
            source="sample",
        ),
        ProviderSnapshot(
            id="cursor",
            name="Cursor",
            icon="CU",
            used=74,
            window="Fast requests",
            reset="Resets tomorrow",
            detail="Sample detail screen",
            source="sample",
        ),
    ]


def _serialize_snapshot(providers: list[ProviderSnapshot]) -> str:
    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    ordered = sorted(
        providers,
        key=lambda provider: (PREFERRED_ORDER.get(provider.id, 99), provider.name.lower()),
    )
    lines = [f"meta|{stamp}"]
    lines.extend(provider.to_line() for provider in ordered)
    return "\n".join(lines) + "\n"


def _resolve_output_path(output: str | None, flipper_root: str | None) -> Path:
    if output:
        return Path(output).expanduser()
    if flipper_root:
        return Path(flipper_root).expanduser() / "apps_data" / APP_ID / "usage.txt"
    return DEFAULT_OUTPUT


def _write_snapshot(path: Path, providers: list[ProviderSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_snapshot(providers), encoding="utf-8")


def _resolve_usb_storage_script() -> Path:
    override = os.environ.get("FLIPPER_STORAGE_SCRIPT")
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return path

    default = Path.home() / ".ufbt" / "current" / "scripts" / "storage.py"
    if default.exists():
        return default

    raise RuntimeError(
        "could not find Flipper storage.py helper; set FLIPPER_STORAGE_SCRIPT to its full path"
    )


def _usb_push_snapshot(args: argparse.Namespace, snapshot_text: str) -> str:
    storage_script = _resolve_usb_storage_script()

    with tempfile.TemporaryDirectory(prefix="ai-dashboard-usb-") as temp_dir:
        local_snapshot = Path(temp_dir) / "usage.txt"
        local_snapshot.write_text(snapshot_text, encoding="utf-8")

        command = " ".join(
            [
                'eval "$(ufbt -s env)"',
                "&&",
                "python3",
                shlex.quote(str(storage_script)),
                "-p",
                shlex.quote(args.port),
                "send",
                shlex.quote(str(local_snapshot)),
                shlex.quote(USB_DESTINATION),
            ]
        )

        completed = subprocess.run(
            command,
            shell=True,
            executable="/bin/zsh",
            text=True,
            capture_output=True,
            check=False,
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"USB push failed: {detail or f'exit code {completed.returncode}'}")

    output = (completed.stdout or "").strip()
    if output:
        return output.splitlines()[-1]
    return args.port


async def _ble_push_snapshot(args: argparse.Namespace, snapshot_text: str) -> str:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as error:  # pragma: no cover - depends on local env
        raise RuntimeError("BLE push requires the 'bleak' package. Install it with: pip install bleak") from error

    target: Any | None = args.address
    if target is None:
        devices = await BleakScanner.discover(timeout=args.scan_timeout)
        query = args.name.lower()
        for device in devices:
            if query in (device.name or "").lower():
                target = device
                break
        if target is None:
            raise RuntimeError(f"no BLE device found matching name fragment {args.name!r}")

    async with BleakClient(target) as client:
        if not client.is_connected:
            raise RuntimeError("BLE connection failed")

        payload = (snapshot_text + BLE_END_MARKER).encode("utf-8")
        for offset in range(0, len(payload), BLE_CHUNK_SIZE):
            chunk = payload[offset : offset + BLE_CHUNK_SIZE]
            await client.write_gatt_char(BLE_SERIAL_TX_UUID, chunk, response=True)
            await asyncio.sleep(0.03)

        await asyncio.sleep(0.2)
        return getattr(client, "address", args.address or args.name)


def _collect_providers(args: argparse.Namespace) -> list[ProviderSnapshot]:
    providers_by_id: dict[str, ProviderSnapshot] = {}

    collectors: list[tuple[str, Any]] = []
    if not args.skip_codex:
        collectors.append(("codex", CodexCollector()))
    if not args.skip_claude:
        collectors.append(("claude", ClaudeCollector()))

    for name, collector in collectors:
        if not collector.is_available():
            continue
        try:
            for provider in collector.collect():
                providers_by_id[provider.id] = provider
        except Exception as error:  # noqa: BLE001
            print(f"[warn] {name} collector failed: {error}", file=sys.stderr)

    manual_path = Path(args.manual).expanduser()
    try:
        for provider in _load_manual_providers(manual_path):
            providers_by_id[provider.id] = provider
    except Exception as error:  # noqa: BLE001
        print(f"[warn] manual provider file failed: {error}", file=sys.stderr)

    providers = list(providers_by_id.values())
    if not providers and args.sample_if_empty:
        providers = _sample_providers()
    return providers


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect AI usage and write a Flipper-friendly snapshot file."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample_parser = subparsers.add_parser("sample", help="write a sample snapshot")
    sample_parser.add_argument("--output", help="write to an explicit file path")
    sample_parser.add_argument("--flipper-root", help="mounted Flipper SD card root")

    collect_parser = subparsers.add_parser("collect", help="collect available provider data")
    collect_parser.add_argument("--output", help="write to an explicit file path")
    collect_parser.add_argument("--flipper-root", help="mounted Flipper SD card root")
    collect_parser.add_argument(
        "--manual",
        default=str(DEFAULT_MANUAL),
        help="manual providers JSON file",
    )
    collect_parser.add_argument(
        "--sample-if-empty",
        action="store_true",
        help="write sample providers when no collector returns data",
    )
    collect_parser.add_argument("--skip-codex", action="store_true", help="disable the codex collector")
    collect_parser.add_argument("--skip-claude", action="store_true", help="disable the claude collector")

    ble_parser = subparsers.add_parser("ble-push", help="collect provider data and push it to a Flipper over BLE")
    ble_parser.add_argument(
        "--manual",
        default=str(DEFAULT_MANUAL),
        help="manual providers JSON file",
    )
    ble_parser.add_argument(
        "--sample-if-empty",
        action="store_true",
        help="write sample providers when no collector returns data",
    )
    ble_parser.add_argument("--skip-codex", action="store_true", help="disable the codex collector")
    ble_parser.add_argument("--skip-claude", action="store_true", help="disable the claude collector")
    ble_parser.add_argument("--address", help="exact BLE address of the Flipper")
    ble_parser.add_argument(
        "--name",
        default="Flipper",
        help="BLE device name fragment to scan for when --address is not provided",
    )
    ble_parser.add_argument(
        "--scan-timeout",
        type=float,
        default=6.0,
        help="BLE scan timeout in seconds when discovering by name",
    )
    ble_parser.add_argument("--output", help="also write the collected snapshot to an explicit file path")

    usb_parser = subparsers.add_parser("usb-push", help="collect provider data and push it to a Flipper over USB")
    usb_parser.add_argument(
        "--manual",
        default=str(DEFAULT_MANUAL),
        help="manual providers JSON file",
    )
    usb_parser.add_argument(
        "--sample-if-empty",
        action="store_true",
        help="write sample providers when no collector returns data",
    )
    usb_parser.add_argument("--skip-codex", action="store_true", help="disable the codex collector")
    usb_parser.add_argument("--skip-claude", action="store_true", help="disable the claude collector")
    usb_parser.add_argument(
        "--port",
        default="auto",
        help="Flipper CDC port, or 'auto' to resolve it with ufbt tooling",
    )
    usb_parser.add_argument("--output", help="also write the collected snapshot to an explicit file path")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "sample":
        providers = _sample_providers()
        output_path = _resolve_output_path(getattr(args, "output", None), getattr(args, "flipper_root", None))
        _write_snapshot(output_path, providers)
        label = "provider" if len(providers) == 1 else "providers"
        print(f"Wrote {len(providers)} {label} to {output_path}")
        return 0

    providers = _collect_providers(args)
    snapshot_text = _serialize_snapshot(providers)

    if args.command == "ble-push":
        if getattr(args, "output", None):
            output_path = Path(args.output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(snapshot_text, encoding="utf-8")
        target = asyncio.run(_ble_push_snapshot(args, snapshot_text))
        label = "provider" if len(providers) == 1 else "providers"
        print(f"Pushed {len(providers)} {label} over BLE to {target}")
        return 0

    if args.command == "usb-push":
        if getattr(args, "output", None):
            output_path = Path(args.output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(snapshot_text, encoding="utf-8")
        target = _usb_push_snapshot(args, snapshot_text)
        label = "provider" if len(providers) == 1 else "providers"
        print(f"Pushed {len(providers)} {label} over USB to {USB_DESTINATION}")
        if target:
            print(target)
        return 0

    output_path = _resolve_output_path(getattr(args, "output", None), getattr(args, "flipper_root", None))
    _write_snapshot(output_path, providers)

    label = "provider" if len(providers) == 1 else "providers"
    print(f"Wrote {len(providers)} {label} to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
