#!/usr/bin/env python3
"""Check and update a skill from GitHub Releases.

Usage:
  python3 scripts/skill_update.py check --repo owner/repo
  python3 scripts/skill_update.py update --repo owner/repo
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def parse_version(value: str) -> Tuple[int, int, int]:
    # Accept "1.2.3", "v1.2.3", "release-1.2.3" and normalize to (major, minor, patch).
    nums = [int(x) for x in re.findall(r"\d+", value)]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def read_frontmatter(skill_md: Path) -> Dict[str, Any]:
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    if yaml is None:
        # Minimal fallback parser for name/description only; metadata may be unavailable.
        result: Dict[str, Any] = {}
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip().strip('"')
        return result
    parsed = yaml.safe_load(m.group(1))
    return parsed if isinstance(parsed, dict) else {}


def get_local_version(skill_dir: Path) -> str:
    data = read_frontmatter(skill_dir / "SKILL.md")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    version = meta.get("version") if isinstance(meta, dict) else None
    if isinstance(version, str) and version.strip():
        return version.strip()
    return "0.0.0"


def github_api_get(url: str, token: Optional[str]) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "weex-trader-skill-updater/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API error {exc.code}: {body}") from exc


def find_release_asset(release: Dict[str, Any], asset_name: str) -> Dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SystemExit("Invalid release payload: assets missing")
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == asset_name:
            return asset
    raise SystemExit(f"Release asset not found: {asset_name}")


def fetch_latest_release(repo: str, asset_name: str, token: Optional[str]) -> Dict[str, Any]:
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    release = github_api_get(api_url, token)
    asset = find_release_asset(release, asset_name)
    tag = str(release.get("tag_name") or "0.0.0")
    return {
        "tag": tag,
        "version": tag,
        "asset_name": asset_name,
        "asset_url": asset.get("browser_download_url"),
        "published_at": release.get("published_at"),
    }


def download_file(url: str, out_file: Path, token: Optional[str]) -> None:
    headers = {"User-Agent": "weex-trader-skill-updater/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        out_file.write_bytes(resp.read())


def detect_extracted_skill_dir(extract_dir: Path) -> Path:
    entries = [p for p in extract_dir.iterdir() if p.is_dir() and not p.name.startswith("__")]
    if not entries:
        raise SystemExit("Invalid skill package: no top-level directory")
    if len(entries) == 1:
        return entries[0]
    # Fallback: choose folder that contains SKILL.md
    for p in entries:
        if (p / "SKILL.md").exists():
            return p
    raise SystemExit("Invalid skill package: cannot locate skill root directory")


def install_skill(skill_file: Path, install_dir: Path) -> Dict[str, str]:
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skill-update-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(skill_file, "r") as zf:
            zf.extractall(tmp_path)

        incoming_skill_dir = detect_extracted_skill_dir(tmp_path)
        skill_name = incoming_skill_dir.name
        target_dir = install_dir / skill_name

        staged_dir = install_dir / f".{skill_name}.new"
        backup_dir = install_dir / f".{skill_name}.bak"

        if staged_dir.exists():
            shutil.rmtree(staged_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

        shutil.copytree(incoming_skill_dir, staged_dir)

        if target_dir.exists():
            target_dir.rename(backup_dir)
        staged_dir.rename(target_dir)

        if backup_dir.exists():
            shutil.rmtree(backup_dir)

        return {
            "skill_name": skill_name,
            "installed_to": str(target_dir),
        }


def default_install_dir(skill_dir: Path) -> Path:
    # If this script runs inside an installed skill folder, install_dir is parent of skill_dir.
    return skill_dir.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skill update helper via GitHub releases")
    parser.add_argument("--skill-dir", default=str(Path(__file__).resolve().parent.parent))

    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Check latest GitHub release vs local version")
    p_check.add_argument("--repo", required=True, help="owner/repo")
    p_check.add_argument("--asset-name", default="weex-trader-skill.skill")
    p_check.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))

    p_update = sub.add_parser("update", help="Download and install latest skill from GitHub release")
    p_update.add_argument("--repo", required=True, help="owner/repo")
    p_update.add_argument("--asset-name", default="weex-trader-skill.skill")
    p_update.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))
    p_update.add_argument("--install-dir", default=None, help="Defaults to parent of --skill-dir")

    return parser


def cmd_check(args: argparse.Namespace) -> int:
    skill_dir = Path(args.skill_dir).resolve()
    local_version = get_local_version(skill_dir)
    remote = fetch_latest_release(args.repo, args.asset_name, args.token)

    local_v = parse_version(local_version)
    remote_v = parse_version(str(remote["version"]))
    update_available = remote_v > local_v

    payload = {
        "skill_dir": str(skill_dir),
        "local_version": local_version,
        "remote_tag": remote["tag"],
        "remote_version": remote["version"],
        "asset_name": args.asset_name,
        "asset_url": remote["asset_url"],
        "update_available": update_available,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    skill_dir = Path(args.skill_dir).resolve()
    install_dir = Path(args.install_dir).resolve() if args.install_dir else default_install_dir(skill_dir)

    local_version = get_local_version(skill_dir)
    remote = fetch_latest_release(args.repo, args.asset_name, args.token)

    if parse_version(str(remote["version"])) <= parse_version(local_version):
        print(
            json.dumps(
                {
                    "updated": False,
                    "reason": "already_latest",
                    "local_version": local_version,
                    "remote_version": remote["version"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    asset_url = str(remote["asset_url"])
    if not asset_url:
        raise SystemExit("Release asset has no downloadable URL")

    with tempfile.TemporaryDirectory(prefix="skill-download-") as tmp:
        skill_file = Path(tmp) / args.asset_name
        download_file(asset_url, skill_file, args.token)
        install_result = install_skill(skill_file, install_dir)

    print(
        json.dumps(
            {
                "updated": True,
                "from_version": local_version,
                "to_version": remote["version"],
                "repo": args.repo,
                "asset_name": args.asset_name,
                "install": install_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    if args.command == "update":
        return cmd_update(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
