#!/usr/bin/env python3
"""
generate_clean_final.py
Generates a clean, deduplicated, schema‑consistent training dataset.
- Only valid groupby: dept_name, status, role, shift (and NONE)
- B‑prefixed slot tags (B-PERSON, B-STATUS, B-TIME, B-DEPT, B-ROLE)
- No duplicates
- Strong slot annotation
- Balanced intent distribution
"""

import random
import re
import json
from typing import List, Dict, Tuple

# ---------------------------------------------------------------------------
# ONTOLOGY – based on actual employee_attendance_v / employee_leave_v schema
# ---------------------------------------------------------------------------
PERSONS = [
    "Amit Sharma", "Priya Nair", "Rajesh Kumar", "Neha Iyer", "Rohan Mehta",
    "Ananya Singh", "Kavita Rao", "Arjun Verma", "Sneha Patel", "Vikram Gupta",
    "Meera Reddy", "Sanjay Joshi", "Deepika Das", "Nikhil Malhotra",
    "Shreya Bhat", "Gaurav Chopra", "Pooja Shah", "Manish Kapoor",
    "Swati Agarwal", "Siddharth Sen",
    # Add more if you like
]

DEPARTMENTS = [
    "Human Resources", "Finance", "Accounts", "Operations", "Sales", "Admin",
    "IT", "Marketing", "Production", "Quality", "Legal", "R&D",
    "Heavy Electrical Equip. Plant", "Transformer Plant", "Nuclear Plant",
    "Steel Plates Plant", "Internship and Training", "Public Relations",
    "Research and Development", "Management", "Security"
]

# Department typos → correct form (still used as noisy input)
DEPARTMENT_TYPOS = {
    "hr departmenr": "Human Resources",
    "human resouces": "Human Resources",
    "finace": "Finance",
    "operatons": "Operations",
    "saless": "Sales",
    "admn": "Admin",
    "humen resources": "Human Resources",
}

STATUS_ATTENDANCE = ["absent", "present", "late", "half day", "on duty", "WFH", "late mark"]
STATUS_LEAVE = ["approved", "pending", "rejected", "cancelled", "requested"]

TIME_HINTS = [
    "today", "yesterday", "this week", "last week", "this month", "last month",
    "this year", "last year", "last quarter", "next week", "next month",
    "in March", "in April", "from last Monday", "between 1st May and 15th May",
    "from January to March", "in the first half of the month",
    "for the previous 30 days"
]

SYNONYMS_EXECUTIVE = ["executives", "staff", "employees", "workers", "people"]

# Valid groupby values your model actually accepts:
VALID_GROUPBY = ["NONE", "dept_name", "status", "role", "shift"]
# We'll map natural words to these
GROUPBY_MAP = {
    "department": "dept_name",
    "status": "status",
    "role": "role",
    "shift": "shift",
}

# ---------------------------------------------------------------------------
# Slot helper – always use B‑ prefix tags
# ---------------------------------------------------------------------------
TAG_MAP = {
    "PERSON": "B-PERSON",
    "STATUS": "B-STATUS",
    "TIME":   "B-TIME",
    "DEPT":   "B-DEPT",
    "ROLE":   "B-ROLE",
}

def find_span(utt: str, phrase: str) -> Tuple[int, int]:
    """Return (start, end) of phrase in utt (case‑insensitive)."""
    start = utt.lower().find(phrase.lower())
    if start != -1:
        return start, start + len(phrase)
    # fallback whole‑word regex
    m = re.search(r'\b' + re.escape(phrase) + r'\b', utt, re.IGNORECASE)
    if m:
        return m.start(), m.end()
    return -1, -1

def make_slots(utt: str, span_map: Dict[str, str]) -> List[Tuple[int, int, str]]:
    """Create sorted list of (start, end, B‑tag)."""
    slots = []
    for phrase, raw_tag in span_map.items():
        s, e = find_span(utt, phrase)
        if s != -1:
            tag = TAG_MAP.get(raw_tag, raw_tag)
            slots.append((s, e, tag))
    slots.sort(key=lambda x: x[0])
    return slots

# ---------------------------------------------------------------------------
# GENERATORS
# ---------------------------------------------------------------------------
def gen_person_name_query():
    person = random.choice(PERSONS)
    # leave or attendance intent
    if random.random() < 0.5:
        tmpl = random.choice([
            "show leaves of {p}",
            "show leave history of {p}",
            "give me {p}'s leave details",
            "fetch leave requests for {p}",
            "show approved leaves for {p}",
            "display leaves for {p}",
        ])
        intent = "leave_summary"
    else:
        tmpl = random.choice([
            "show attendance of {p}",
            "fetch attendance of {p}",
            "show record for {p}",
            "display attendance for {p}",
            "give me {p}'s attendance details",
            "show absences for {p}",
            "show attendance of {p} today",
            "show attendance of {p} this week",
        ])
        intent = "attendance_summary"
    utt = tmpl.format(p=person)
    slots = make_slots(utt, {person: "PERSON"})
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": slots,
    }

def gen_department_query():
    if random.random() < 0.2:   # typos
        typo, correct = random.choice(list(DEPARTMENT_TYPOS.items()))
        dept_str = typo
    else:
        dept_str = random.choice(DEPARTMENTS)
    if random.random() < 0.6:
        tmpl = random.choice([
            "show attendance of {d} department",
            "show attendance of {d}",
            "list attendance for {d}",
        ])
        intent = "attendance_summary"
    else:
        tmpl = random.choice([
            "show leaves in {d}",
            "show leave requests for {d}",
            "list leaves in {d}",
        ])
        intent = "leave_summary"
    utt = tmpl.format(d=dept_str)
    slots = make_slots(utt, {dept_str: "DEPT"})
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": slots,
    }

def gen_status_query():
    if random.random() < 0.5:
        status = random.choice(STATUS_ATTENDANCE)
        intent = "attendance_summary"
        domain = "employees"
    else:
        status = random.choice(STATUS_LEAVE)
        intent = "leave_summary"
        domain = "leaves"
    tmpl = random.choice([
        "show {s} {d}",
        "list {s} {d}",
        "who is {s} today",
        "who has {s} {d}",
        "show all {s} {d} records",
        "give me the {s} {d} list",
    ])
    utt = tmpl.format(s=status, d=domain)
    utt = re.sub(r'\s+', ' ', utt).strip()
    slots = make_slots(utt, {status: "STATUS"})
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": slots,
    }

def gen_executive_synonym():
    word = random.choice(SYNONYMS_EXECUTIVE)
    dept = random.choice(DEPARTMENTS)
    tmpl = random.choice([
        "show {w}",
        "list {w}",
        "find {w} in {d}",
        "show {w} on leave",
        "show {w} absent today",
        "show {w} with approved leave",
    ])
    utt = tmpl.format(w=word, d=dept)
    # Proper intent: employee list = employee_lookup
    intent = "employee_lookup"
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": [],
    }

def gen_grouping_query():
    # Only valid groupby words
    group_word, groupby_label = random.choice(list(GROUPBY_MAP.items()))
    obj = random.choice(["employees", "attendance", "leaves", "absences"])
    tmpl = random.choice([
        "group {o} by {g}",
        "count {o} by {g}",
        "show {g}-wise {o}",
        "how many {o} per {g}",
    ])
    utt = tmpl.format(o=obj, g=group_word)
    intent = "department_summary"
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "AGGREGATE",
        "aggregation": "COUNT",
        "groupby": groupby_label,
        "ranking": "NONE",
        "trend": "NONE",
        "slots": [],
    }

def gen_ranking_query():
    top = random.choice(["top", "most", "highest"])
    bottom = random.choice(["bottom", "least", "lowest"])
    if random.random() < 0.5:
        rank_word = top
        ranking = "TOP"
    else:
        rank_word = bottom
        ranking = "BOTTOM"
    tmpl = random.choice([
        "{r} departments by attendance",
        "{r} departments by absenteeism",
        "{r} employees by leave count",
        "show the {r} absent employees",
        "which department has the {r} absences",
        "give me the {r} 5 departments by attendance",
    ])
    utt = tmpl.format(r=rank_word)
    return {
        "utterance": utt,
        "intent": "department_summary",
        "mode": "AGGREGATE",
        "aggregation": "COUNT",
        "groupby": "dept_name",
        "ranking": ranking,
        "trend": "NONE",
        "slots": [],
    }

def gen_time_query():
    time_expr = random.choice(TIME_HINTS)
    if random.random() < 0.5:
        intent = "attendance_summary"
        tmpl = random.choice([
            "show attendance {t}",
            "fetch attendance {t}",
            "attendance records {t}",
        ])
    else:
        intent = "leave_summary"
        tmpl = random.choice([
            "show leaves {t}",
            "list leaves {t}",
            "leave requests {t}",
        ])
    utt = tmpl.format(t=time_expr)
    slots = make_slots(utt, {time_expr: "TIME"})
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": slots,
    }

def gen_paraphrase():
    pool = [
        ("who's absent", "attendance_summary"),
        ("who is not present", "attendance_summary"),
        ("show people who did not come", "attendance_summary"),
        ("find who is on leave", "leave_summary"),
        ("tell me who took leave", "leave_summary"),
        ("give me the leave list", "leave_summary"),
        ("show me the attendance sheet", "attendance_summary"),
        ("who's off today", "leave_summary"),
        ("who took a day off", "leave_summary"),
        ("who got approved leave", "leave_summary"),
        ("who is working today", "attendance_summary"),
    ]
    utt, intent = random.choice(pool)
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": [],
    }

def gen_typo_query():
    pool = [
        ("show attendence of hr departmnt", "attendance_summary"),
        ("show approved leavs", "leave_summary"),
        ("show absent employes", "attendance_summary"),
        ("show attendnace today", "attendance_summary"),
        ("show rejected leavs", "leave_summary"),
        ("show pending laeves", "leave_summary"),
        ("show absent stafff", "attendance_summary"),
        ("show all aprroved leaves", "leave_summary"),
        ("show all pendng leaves", "leave_summary"),
        ("show all absnt employees", "attendance_summary"),
        ("show attendance of humen resources", "attendance_summary"),
    ]
    utt, intent = random.choice(pool)
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": [],
    }

def gen_multi_filter():
    person = random.choice(PERSONS)
    status = random.choice(STATUS_ATTENDANCE + STATUS_LEAVE)
    dept = random.choice(DEPARTMENTS)
    time_expr = random.choice(TIME_HINTS) if random.random() < 0.4 else ""
    tmpl = random.choice([
        "show {s} leaves of {p} in {d} {t}",
        "show {s} employees in {d} {t}",
        "show attendance of {p} in {d} {t}",
        "show {s} leave requests for {p} {t}",
        "give me {p}'s {s} attendance in {d}",
        "fetch {s} records of {p} from {d} {t}",
    ])
    utt = tmpl.format(s=status, p=person, d=dept, t=time_expr)
    utt = re.sub(r'\s+', ' ', utt).strip()
    # determine intent
    if status in STATUS_LEAVE:
        intent = "leave_summary"
    else:
        intent = "attendance_summary"
    slots_map = {}
    if person in utt:
        slots_map[person] = "PERSON"
    if dept in utt:
        slots_map[dept] = "DEPT"
    if time_expr and time_expr in utt:
        slots_map[time_expr] = "TIME"
    if status in utt:
        slots_map[status] = "STATUS"
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": make_slots(utt, slots_map),
    }

def gen_conversational():
    person = random.choice(PERSONS)
    dept = random.choice(DEPARTMENTS)
    pool = [
        ("can you show me {p}'s leaves", "leave_summary"),
        ("can you show me who is absent today", "attendance_summary"),
        ("can you list the approved leaves", "leave_summary"),
        ("can you give me attendance for {d}", "attendance_summary"),
        ("i need the absent employees list", "attendance_summary"),
        ("i need leave requests for {p}", "leave_summary"),
        ("i want attendance of the {d} department", "attendance_summary"),
        ("i want approved leaves", "leave_summary"),
    ]
    tmpl, intent = random.choice(pool)
    utt = tmpl.format(p=person, d=dept)
    slots_map = {}
    if person in utt:
        slots_map[person] = "PERSON"
    if dept in utt:
        slots_map[dept] = "DEPT"
    return {
        "utterance": utt,
        "intent": intent,
        "mode": "LIST",
        "aggregation": "NONE",
        "groupby": "NONE",
        "ranking": "NONE",
        "trend": "NONE",
        "slots": make_slots(utt, slots_map),
    }

def gen_vivid():
    pool = [
        ("show me everyone who did not come in today", "attendance_summary"),
        ("which executives are on leave", "leave_summary"),
        ("which people in HR are absent", "attendance_summary"),
        ("which employees in finance have approved leaves", "leave_summary"),
        ("how many people are absent in sales", "attendance_summary"),
        ("how many leave requests are pending", "leave_summary"),
        ("how many executives are in operations", "employee_lookup"),
        ("how many approved leaves are there this month", "leave_summary"),
        ("which department has the most absences", "department_summary"),
        ("which employee has the highest leave count", "leave_summary"),
        ("give me today's attendance for all departments", "attendance_summary"),
        ("give me this month's leave summary", "leave_summary"),
        ("show the absences in the HR team", "attendance_summary"),
        ("show the people on leave in operations", "leave_summary"),
    ]
    utt, intent = random.choice(pool)
    # set mode based on content
    if "most" in utt or "highest" in utt:
        mode = "AGGREGATE"
        aggregation = "COUNT"
        groupby = "dept_name" if "department" in utt else "employee"
        ranking = "TOP"
    else:
        mode = "LIST"
        aggregation = "NONE"
        groupby = "NONE"
        ranking = "NONE"
    return {
        "utterance": utt,
        "intent": intent,
        "mode": mode,
        "aggregation": aggregation,
        "groupby": groupby,
        "ranking": ranking,
        "trend": "NONE",
        "slots": [],
    }

# ---------------------------------------------------------------------------
# BALANCED GENERATION + DEDUPLICATION
# ---------------------------------------------------------------------------
def generate_all(target: int = 12000):
    random.seed(42)
    seen = set()
    examples = []
    while len(examples) < target:
        r = random.random()
        if r < 0.15:
            ex = gen_person_name_query()
        elif r < 0.30:
            ex = gen_department_query()
        elif r < 0.45:
            ex = gen_status_query()
        elif r < 0.55:
            ex = gen_executive_synonym()
        elif r < 0.65:
            ex = gen_grouping_query()
        elif r < 0.75:
            ex = gen_ranking_query()
        elif r < 0.82:
            ex = gen_time_query()
        elif r < 0.88:
            ex = gen_multi_filter()
        elif r < 0.92:
            ex = gen_conversational()
        elif r < 0.96:
            ex = gen_paraphrase()
        else:
            ex = gen_typo_query() if random.random() < 0.5 else gen_vivid()

        utt = ex["utterance"].strip()
        if len(utt) < 3 or utt in seen:
            continue
        seen.add(utt)
        # guarantee slots list exists
        ex.setdefault("slots", [])
        examples.append(ex)
    return examples

if __name__ == "__main__":
    data = generate_all(10000)
    with open("cleaned_training_data.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Saved {len(data)} clean, deduplicated examples to cleaned_training_data.json")