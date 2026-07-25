#!/usr/bin/env python3
"""Convert Mihomo rule-set YAML files to .mrs next to the source.

Rules:
- Scan all *.yaml / *.yml outside .github/
- Expect standard rule-set shape with a `payload` list
- Auto-detect type: domain vs ipcidr from payload content
- Write <same-name>.mrs beside the YAML
- Delete orphan .mrs whose YAML is gone
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise

SKIP_DIR_NAMES = {".git", ".github", ".venv", "venv", "node_modules"}
YAML_SUFFIXES = {".yaml", ".yml"}
DOMAIN_HINT = re.compile(
    r"^(?:\+\.)?(?:\*\.)?(?:[A-Za-z0-9_-]+\.)+[A-Za-z]{2,}(?:\.[A-Za-z]{2,})?$|"
    r"^(?:\+\.)?(?:\*\.)?[A-Za-z0-9_-]+$"
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def iter_yaml_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        base = Path(dirpath)
        if any(part in SKIP_DIR_NAMES for part in base.relative_to(root).parts):
            continue
        for name in filenames:
            path = base / name
            if path.suffix.lower() in YAML_SUFFIXES:
                files.append(path)
    return sorted(files)


def load_payload(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        raise ValueError(f"{path}: empty file")
    if not isinstance(data, dict) or "payload" not in data:
        raise ValueError(f"{path}: expected a mapping with top-level `payload`")
    payload = data["payload"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path}: `payload` must be a non-empty list")
    items: list[str] = []
    for idx, item in enumerate(payload):
        if item is None:
            raise ValueError(f"{path}: payload[{idx}] is null")
        text = str(item).strip()
        if not text:
            raise ValueError(f"{path}: payload[{idx}] is empty")
        # classical full rule lines are not supported for mrs conversion here
        if "," in text and text.split(",", 1)[0].upper() in {
            "DOMAIN",
            "DOMAIN-SUFFIX",
            "DOMAIN-KEYWORD",
            "DOMAIN-REGEX",
            "GEOSITE",
            "IP-CIDR",
            "IP-CIDR6",
            "GEOIP",
            "SRC-IP-CIDR",
            "PROCESS-NAME",
            "RULE-SET",
            "MATCH",
        }:
            raise ValueError(
                f"{path}: payload uses full rule syntax `{text}`; "
                "use plain domain / ipcidr values under payload"
            )
        items.append(text)
    return items


def is_ipcidr(value: str) -> bool:
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def detect_type(items: list[str], path: Path) -> str:
    ip_count = sum(1 for item in items if is_ipcidr(item))
    if ip_count == len(items):
        return "ipcidr"
    if ip_count == 0:
        # soft check: warn-looking domains still allowed; mihomo validates later
        return "domain"
    raise ValueError(
        f"{path}: mixed domain/ipcidr payload "
        f"({ip_count} ipcidr / {len(items) - ip_count} non-ip); split into separate files"
    )


def convert_one(mihomo: Path, yaml_path: Path, dry_run: bool = False) -> Path:
    items = load_payload(yaml_path)
    rule_type = detect_type(items, yaml_path)
    mrs_path = yaml_path.with_suffix(".mrs")
    cmd = [
        str(mihomo),
        "convert-ruleset",
        rule_type,
        "yaml",
        str(yaml_path),
        str(mrs_path),
    ]
    print(f"[convert] {yaml_path.relative_to(repo_root())} -> {mrs_path.name} ({rule_type})")
    if dry_run:
        return mrs_path
    subprocess.run(cmd, check=True)
    if not mrs_path.is_file() or mrs_path.stat().st_size == 0:
        raise RuntimeError(f"failed to produce {mrs_path}")
    return mrs_path


def cleanup_orphans(root: Path, yaml_files: list[Path], dry_run: bool = False) -> list[Path]:
    expected = {p.with_suffix(".mrs").resolve() for p in yaml_files}
    removed: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        base = Path(dirpath)
        for name in filenames:
            if not name.lower().endswith(".mrs"):
                continue
            mrs = (base / name).resolve()
            if mrs in expected:
                continue
            rel = mrs.relative_to(root.resolve())
            print(f"[delete] orphan {rel}")
            removed.append(mrs)
            if not dry_run:
                mrs.unlink(missing_ok=True)
    return removed


def find_mihomo(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"mihomo not found: {path}")
        return path
    for candidate in ("mihomo", "clash-meta"):
        from shutil import which

        found = which(candidate)
        if found:
            return Path(found)
    raise FileNotFoundError("mihomo binary not found; pass --mihomo /path/to/mihomo")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument("--mihomo", default=os.environ.get("MIHOMO_BIN"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    yaml_files = iter_yaml_files(root)
    if not yaml_files:
        print("no yaml rule files found")
        cleanup_orphans(root, [], dry_run=args.dry_run)
        return 0

    mihomo = find_mihomo(args.mihomo)
    errors: list[str] = []
    for path in yaml_files:
        try:
            convert_one(mihomo, path, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - collect all file errors
            errors.append(str(exc))
            print(f"[error] {exc}", file=sys.stderr)

    cleanup_orphans(root, yaml_files, dry_run=args.dry_run)

    if errors:
        print(f"{len(errors)} file(s) failed", file=sys.stderr)
        return 1
    print(f"done: {len(yaml_files)} yaml file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
