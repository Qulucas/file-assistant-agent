#!/usr/bin/env python3
"""Verify the artifacts the agent is expected to produce for T1 and T2.

Pure Python, no LLM involved. Exits 0 when the workspace state matches the
expected outcomes, prints a report otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXPECTED_T1_FILES = {
    "meetings/2025-09-04-migration-standup.md",
    "meetings/2025-10-08-eng-sync.md",
    "meetings/2025-11-13-data-review.md",
    "meetings/2025-11-14-steering.md",
    "meetings/2025-12-07-platform-sync.md",
    "meetings/2026-01-14-cutover-planning.md",
    "meetings/2026-01-22-all-hands.md",
    "notes/falcon-migration-checklist.md",
    "data/2025-10-vendor-tracking.csv",
    "logs/2025-12-full-export.log",
}

EXPECTED_T1_MONTHS = {"2025-09", "2025-10", "2025-11", "2025-12", "2026-01"}

EXPECTED_ARCHIVED = {
    "blog-post-launch.md",
    "onboarding-guide.md",
    "api-v1-spec.md",
}

EXPECTED_DRAFTS_KEPT = {
    "pricing-review-obsolete.md",
    "runbook-backup.md",
    "roadmap-2026.md",
    "design-tokens.md",
    "retention-policy.md",
}


def verify_t1(root: Path) -> list[str]:
    errors: list[str] = []
    index = root / "falcon_index.md"
    if not index.exists():
        return ["falcon_index.md missing"]
    text = index.read_text(encoding="utf-8")
    if "Project Phoenix" not in text:
        errors.append("falcon_index.md does not state the official name Project Phoenix")
    listed = {
        m.group(1).replace("\\ ", " ")
        for m in re.finditer(r"^\s*[-*]\s+([^\s][^—]*?)\s*—", text, re.MULTILINE)
    }
    listed = {p.strip() for p in listed}
    missing = EXPECTED_T1_FILES - listed
    if missing:
        errors.append(f"falcon_index.md missing files: {sorted(missing)}")
    months = set(re.findall(r"^## (\d{4}-\d{2})$", text, re.MULTILINE))
    missing_months = EXPECTED_T1_MONTHS - months
    if missing_months:
        errors.append(f"falcon_index.md missing month sections: {sorted(missing_months)}")
    if "birdwatching" in text:
        errors.append("falcon_index.md wrongly includes the birdwatching note")
    return errors


def verify_t2(root: Path) -> list[str]:
    errors: list[str] = []
    archive = root / "archive"
    if not archive.is_dir():
        return ["archive/ missing"]
    archived = {p.name for p in archive.iterdir() if p.is_file() and p.name != "MANIFEST.md"}
    if archived != EXPECTED_ARCHIVED:
        errors.append(f"archive/ files mismatch: {sorted(archived)}")
    manifest = archive / "MANIFEST.md"
    if not manifest.exists():
        errors.append("archive/MANIFEST.md missing")
    else:
        lines = [l for l in manifest.read_text(encoding="utf-8").splitlines() if l.strip()]
        names = {re.sub(r"^\s*[-*]\s+", "", l).strip() for l in lines if l.strip().startswith(("-", "*"))}
        if names != EXPECTED_ARCHIVED:
            errors.append(f"MANIFEST.md entries mismatch: {sorted(names)}")
    drafts = root / "drafts"
    kept = {p.name for p in drafts.iterdir() if p.is_file()} if drafts.is_dir() else set()
    if kept != EXPECTED_DRAFTS_KEPT:
        errors.append(f"drafts/ kept files mismatch: {sorted(kept)}")
    return errors


def verify_untouched(root: Path) -> list[str]:
    errors: list[str] = []
    for rel in ("logs/2025-09-deploy.log", "logs/2026-01-cron.log",
                "notes/falcon-migration-checklist.md", "data/2025-09-cloud-costs.csv"):
        if not (root / rel).exists():
            errors.append(f"{rel} was removed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify T1/T2 artifacts")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    root = Path(args.workspace)
    errors = verify_t1(root) + verify_t2(root) + verify_untouched(root)
    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: T1 and T2 artifacts verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
