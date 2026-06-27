"""
metadata.py – Shared metadata for Bhelviz pipeline.

At application startup, you may replace the static list below with a dynamic
load from Oracle (e.g. querying employee_attendance_v). Until then, this
hard‑coded set is used.
"""

# ── Static department list (your actual departments) ──────────────────────────
KNOWN_DEPARTMENTS: set[str] = {
    "Human Resources",
    "DTG – Digital Trans. Group",
    "Heavy Electrical Equip. Plant",
    "Internship and Training",
    "Management",
    "Nuclear Plant",
    "Public Relations",
    "Research and Development",
    "Security",
    "Steel Plates Plant",
    "Transformer Plant",
}