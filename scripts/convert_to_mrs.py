#!/usr/bin/env python3
"""Convert rule-set YAML files to Mihomo .mrs and sing-box .srs files.

Rules:
- Scan all *.yaml / *.yml outside .github/
- Expect standard rule-set shape with a `payload` list
- Auto-detect type: domain vs ipcidr from payload content
- Write <same-name>.mrs and <same-name>.srs beside the YAML
- Delete orphan .mrs/.srs files whose YAML is gone
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
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


def build_sing_box_ruleset(items: list[str], rule_type: str, path: Path) -> dict[str, object]:
    if rule_type == "ipcidr":
        rule: dict[str, object] = {"ip_cidr": items}
    else:
        exact: list[str] = []
        suffix: list[str] = []
        regex: list[str] = []
        for item in items:
            if item.startswith("+."):
                suffix.append(item[2:])
            elif item.startswith("*."):
                domain = item[2:]
                regex.append(r"^(?:[^.]+\.)+" + re.escape(domain) + r"$")
            elif item.startswith("."):
                suffix.append(item[1:])
            elif "*" in item or "+" in item:
                raise ValueError(f"{path}: unsupported domain wildcard `{item}` for sing-box")
            else:
                exact.append(item)

        rule = {}
        if exact:
            rule["domain"] = exact
        if suffix:
            rule["domain_suffix"] = suffix
        if regex:
            rule["domain_regex"] = regex

    return {"version": 3, "rules": [rule]}


def compile_srs(
    sing_box: Path,
    yaml_path: Path,
    items: list[str],
    rule_type: str,
    dry_run: bool = False,
) -> Path:
    srs_path = yaml_path.with_suffix(".srs")
    print(f"[convert] {yaml_path.relative_to(repo_root())} -> {srs_path.name} (sing-box)")
    if dry_run:
        return srs_path

    source = build_sing_box_ruleset(items, rule_type, yaml_path)
    with tempfile.TemporaryDirectory(prefix="sing-box-ruleset-") as tmpdir:
        source_path = Path(tmpdir) / f"{yaml_path.stem}.json"
        output_path = Path(tmpdir) / srs_path.name
        source_path.write_text(json.dumps(source, ensure_ascii=True), encoding="utf-8")
        subprocess.run(
            [
                str(sing_box),
                "rule-set",
                "compile",
                "--output",
                str(output_path),
                str(source_path),
            ],
            check=True,
        )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"failed to produce {srs_path}")
        output_path.replace(srs_path)
    return srs_path


def convert_one(
    mihomo: Path,
    sing_box: Path,
    yaml_path: Path,
    dry_run: bool = False,
) -> tuple[Path, Path]:
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
        srs_path = compile_srs(sing_box, yaml_path, items, rule_type, dry_run=True)
        return mrs_path, srs_path
    subprocess.run(cmd, check=True)
    if not mrs_path.is_file() or mrs_path.stat().st_size == 0:
        raise RuntimeError(f"failed to produce {mrs_path}")
    srs_path = compile_srs(sing_box, yaml_path, items, rule_type)
    return mrs_path, srs_path


def cleanup_orphans(root: Path, yaml_files: list[Path], dry_run: bool = False) -> list[Path]:
    expected = {
        p.with_suffix(suffix).resolve()
        for p in yaml_files
        for suffix in (".mrs", ".srs")
    }
    removed: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        base = Path(dirpath)
        for name in filenames:
            if Path(name).suffix.lower() not in {".mrs", ".srs"}:
                continue
            generated = (base / name).resolve()
            if generated in expected:
                continue
            rel = generated.relative_to(root.resolve())
            print(f"[delete] orphan {rel}")
            removed.append(generated)
            if not dry_run:
                generated.unlink(missing_ok=True)
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


def find_sing_box(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"sing-box not found: {path}")
        return path

    from shutil import which

    found = which("sing-box")
    if found:
        return Path(found)
    raise FileNotFoundError("sing-box binary not found; pass --sing-box /path/to/sing-box")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument("--mihomo", default=os.environ.get("MIHOMO_BIN"))
    parser.add_argument("--sing-box", default=os.environ.get("SING_BOX_BIN"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    yaml_files = iter_yaml_files(root)
    if not yaml_files:
        print("no yaml rule files found")
        cleanup_orphans(root, [], dry_run=args.dry_run)
        return 0

    mihomo = find_mihomo(args.mihomo)
    sing_box = find_sing_box(args.sing_box)
    errors: list[str] = []
    for path in yaml_files:
        try:
            convert_one(mihomo, sing_box, path, dry_run=args.dry_run)
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
