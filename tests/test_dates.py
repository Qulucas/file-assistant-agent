from falcon_agent.dates import extract_month, group_by_month

FM_LEAD = """---
date: 2025-10-04
---
# Falcon migration checklist
"""

FM_UPDATED = """---
status: obsolete
updated: 2025-11-07
---
# Draft
"""

BODY_DATE = "# All Hands\n\nDate: 2026-01-22\nAttendees: Ingrid\n"

FILENAME_CSV = "vendor,contract_end,project,owner\nMeridian,2026-02-01,Project Falcon,Jules\n"

LOG_LINES = (
    "2025-12-01T00:00:06Z svc=ingest level=INFO msg=hello\n"
    "2025-12-02T01:00:00Z svc=api level=INFO msg=world\n"
)


def test_frontmatter_date():
    assert extract_month("notes/x.md", FM_LEAD) == "2025-10"


def test_frontmatter_updated():
    assert extract_month("drafts/x.md", FM_UPDATED) == "2025-11"


def test_body_date_line():
    assert extract_month("meetings/2026-01-22-all-hands.md", BODY_DATE) == "2026-01"


def test_filename_fallback_csv():
    assert extract_month("data/2025-10-vendor-tracking.csv", FILENAME_CSV) == "2025-10"


def test_timestamp_from_log_content():
    assert extract_month("logs/whatever.log", LOG_LINES) == "2025-12"


def test_body_date_precedes_filename():
    assert extract_month("meetings/2026-01-22-all-hands.md", BODY_DATE) == "2026-01"


def test_priority_frontmatter_over_body():
    content = FM_LEAD + "\nDate: 2026-03-01\n"
    assert extract_month("meetings/2026-01-22.md", content) == "2025-10"


def test_no_date_returns_none():
    assert extract_month("notes/reading-list.md", "# Reading List\n- item\n") is None


def test_empty_content_uses_filename():
    assert extract_month("logs/2025-09-deploy.log", "") == "2025-09"


def test_invalid_month_in_filename():
    assert extract_month("notes/2025-13-x.md", "# X\n") is None


def test_group_by_month_sorted_and_skips_none():
    files = [
        ("a.md", "2026-01"),
        ("b.md", "2025-10"),
        ("c.md", None),
        ("d.md", "2025-10"),
    ]
    assert group_by_month(files) == [
        ("2025-10", ["b.md", "d.md"]),
        ("2026-01", ["a.md"]),
    ]
