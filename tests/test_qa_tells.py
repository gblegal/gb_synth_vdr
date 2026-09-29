"""Gate 20 — evidence tells. Every shape tested here is one Project Frithcombe
actually had, or one a fix to it had to keep: the room a blind judge ranked last
on 11 of 16 slots while all nineteen other gates passed."""

import pytest
import yaml

from synthvdr.qa import ALL_GATES
from synthvdr.qa.runner import GateContext, run_gates
from synthvdr.qa.tells import (
    BACKWARD,
    FORWARD,
    LATERAL,
    REPORT,
    THRESHOLDS,
    Role,
    SectionNotes,
    Thresholds,
    content_words,
    document_date,
    evidence_roles,
    gate_20_tells,
    italic_notes,
    note_asymmetry,
    scan_room,
    section_habits,
    selective_bold,
)
from synthvdr.qa.runner import warn
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_distractors, load_findings

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=0
BLIND_TOTAL=0
FLAGGED_TOTAL=0
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="COMM"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="05_commercial"
'''

SUBSTANCE = (
    "Clause 9.4 makes the Supplier indemnify Wexcott against all recall losses, and "
    "clause 9.5 says the indemnity is not subject to any financial cap or limit."
)
ROLES = [Role("COMM-1", True, content_words(f"Uncapped recall indemnity {SUBSTANCE}"))]

SECTION = "05_commercial/5.1_customer-contracts"
CONTRACT = f"{SECTION}/5.1.4_customer-contracts-04.md"
LETTER = f"{SECTION}/5.1.5_customer-contracts-05.md"
ALARM = f"{SECTION}/5.1.6_customer-contracts-06.md"
WAIVER = f"{SECTION}/5.1.7_customer-contracts-07.md"
OTHERS = [f"{SECTION}/5.1.{n}_customer-contracts-0{n}.md" for n in (1, 2, 3)]

FINDING = {
    "id": "COMM-1",
    "title": "Uncapped recall indemnity in the Wexcott supply agreement",
    "severity": "high",
    "workstream": "commercial",
    "multi_document": True,
    "source": CONTRACT,
    "location": "clauses 9.4 and 9.5",
    "substance": SUBSTANCE,
    "corroboration": [LETTER],
}
DISTRACTOR = {
    "id": "DX-1",
    "title": "Change of control termination right in the haulage contract, waived in writing",
    "location": ALARM,
    "resolution": WAIVER,
}


def bold_lines(text, roles=ROLES, thresholds=THRESHOLDS):
    return [t.line for t in selective_bold("doc.md", text, roles, thresholds)]


def clauses(*lines):
    """A contract body: ordinary numbered clauses, then whatever is under test."""
    ordinary = [
        "9.1 The Supplier supplies the Products on the terms of this Agreement.",
        "9.2 The Supplier delivers the Products to the depots named in Schedule 2.",
        "9.3 The Supplier invoices the Customer monthly in arrears.",
    ]
    return "\n\n".join(ordinary + list(lines)) + "\n"


# --- Content words ----------------------------------------------------------


def test_content_words_fold_plurals_drop_function_words_and_normalise_figures():
    words = content_words("The exclusions of £1,200,000 and the Exclusion")
    assert "exclusion" in words and "exclusions" not in words
    assert "the" not in words and "and" not in words
    assert "1200000" in words


# --- Selective bold ---------------------------------------------------------


def test_one_bolded_clause_among_plain_siblings_is_a_tell():
    text = clauses(
        "9.4 **The Supplier indemnifies Wexcott against all recall losses, and the "
        "indemnity is not subject to any financial cap or limit.**"
    )
    assert bold_lines(text) == [7]


def test_bold_on_every_comparable_item_is_house_style():
    text = "\n\n".join(
        f"9.{n} **The Supplier indemnifies Wexcott against recall losses without financial cap {n}.**"
        for n in range(1, 5)
    )
    assert bold_lines(text) == []


def test_only_the_false_qa_answer_is_flagged_never_its_label():
    # Frithcombe's Q&A log: 7 of 62 responses bold, and exactly the false ones.
    text = (
        "**Question.** Please confirm the recall position.\n"
        "**Response.** The recall of 9 May 2025 is closed.\n\n"
        "**Question.** Please confirm the indemnity position.\n"
        "**Response.** **There is no financial cap or limit on the Wexcott recall indemnity.**\n"
    )
    assert bold_lines(text) == [5]


def test_bold_after_a_numbered_run_in_label_is_compared_with_the_other_numbered_labels():
    # The W&I draft bolded exclusions 3, 7, 9 and 12 and no others.
    text = (
        "**2. Forward-looking statements.** Any projection or forecast.\n\n"
        "**3. Disclosed matters.** Any matter fairly disclosed in the Data Room.\n\n"
        "**7. Product recall.** **Any liability under the Wexcott recall indemnity, "
        "which carries no financial cap or limit.**\n\n"
        "**8. Asbestos.** Any liability arising from asbestos.\n"
    )
    assert bold_lines(text) == [5]


def test_a_lone_labelled_line_has_nothing_to_be_uniform_with():
    text = (
        "The commercial team reviews each contract at renewal.\n\n"
        "**Plan.** The team will **renegotiate the uncapped Wexcott recall indemnity clause** at expiry.\n"
    )
    assert bold_lines(text) == [3]


@pytest.mark.parametrize(
    "line",
    [
        "## **Recall indemnity and financial cap**",
        "**Recall indemnity, financial cap and limit for Wexcott**",
        "**Recall indemnity and financial cap.** The Supplier indemnifies Wexcott as follows.",
        '"**Recall Indemnity Cap Losses**" means all losses of Wexcott arising from a recall.',
        "**Recall Indemnity Cap Losses** means all losses of Wexcott arising from a recall.",
        "Wexcott may serve written notice (a **Recall Indemnity Cap Notice**) on the Supplier.",
        "This Agreement is made between **WEXCOTT RECALL INDEMNITY CAP LIMITED** and the Supplier.",
        "**Signed for Wexcott** under the recall indemnity with no financial cap.",
        "The customer is **Wexcott Recall Indemnity Cap Limited**, a company in England.",
        "The position at the recall date: **Recall indemnity cap:** none recorded.",
    ],
    ids=[
        "heading", "bold-only-line", "run-in-label", "quoted-defined-term", "means-defined-term",
        "bracketed-defined-term", "all-caps-party", "signature-label", "company-name", "colon-label",
    ],
)
def test_structure_is_never_a_tell(line):
    assert bold_lines(clauses(line)) == []


def test_a_long_bold_clause_is_not_a_heading_however_it_ends():
    # "2.2 **The aggregate liability ... limited to £1,200,000.**" and
    # "(d) **Financial Indebtedness under finance leases ...;**" were both tells.
    full_stop = clauses(
        "9.5 **The indemnity at clause 9.4 is not subject to any financial cap or limit, "
        "and clause 11.2 does not apply to it.**"
    )
    semicolon = clauses(
        "(d) **the recall costs of Wexcott under the indemnity, which is not subject to any "
        "financial cap or limit of any kind;**",
        "(e) the costs of consumer notices.",
    )
    assert bold_lines(full_stop) == [7]
    assert bold_lines(semicolon) == [7]


def test_a_bold_line_in_the_title_block_is_a_title():
    stamp = "**Wexcott recall indemnity file, prepared for the data room, with no financial cap.**"
    assert bold_lines(f"# Recall correspondence\n\n{stamp}\n\n---\n\n" + clauses()) == []
    # No "#" title, no title block: the same line opening a bare letter is a sentence.
    assert bold_lines(f"{stamp}\n\n" + clauses()) == [1]


def test_bold_sharing_too_few_words_with_the_finding_is_not_a_tell_until_tuned():
    text = clauses("9.4 **The Supplier delivers weekly.**")
    assert bold_lines(text) == []
    assert bold_lines(text, thresholds=Thresholds(bold_overlap=1)) == [7]


TABLE_HEAD = "| Ref | Detail | Status |\n|---|---|---|\n"


def test_one_bold_prose_row_in_a_plain_table_is_a_tell():
    # Frithcombe's NCR log: one near miss bold, in a table where nothing else was.
    text = TABLE_HEAD + (
        "| 1 | Screen damaged; replaced | Closed |\n"
        "| **2** | **Wexcott recall indemnity exposure, no financial cap or limit** | **Open** |\n"
        "| 3 | Seal number not recorded | Closed |\n"
    )
    assert bold_lines(text) == [4]


def test_totals_and_subtotal_rows_are_structure():
    text = "| Item | £m |\n|---|---|\n" + (
        "| Recall indemnity claims | 1.0 |\n"
        "| Financial cap exposure | 2.0 |\n"
        "| **Total recall indemnity and financial cap exposure** | **3.0** |\n"
        "| **Net recall indemnity cap exposure to Wexcott** | **(0.4)** |\n"
    )
    assert bold_lines(text) == []


def test_bold_on_every_row_of_a_kind_is_not_a_tell():
    # Frithcombe's EL schedule bolds exactly the years whose Traced cell says No.
    rows = (
        "| 1978 | Ravelmoor | Yes |\n"
        "| **1979** | **Not traced: Wexcott recall indemnity, no financial cap** | **No** |\n"
        "| **1980** | **Not traced: Wexcott recall indemnity, no financial cap** | **No** |\n"
        "| 1981 | Ravelmoor | Yes |\n"
    )
    assert bold_lines(TABLE_HEAD + rows) == []
    one_untraced_year_left_plain = (
        rows + "| 1982 | Not traced: Wexcott recall indemnity, no financial cap | No |\n"
    )
    assert bold_lines(TABLE_HEAD + one_untraced_year_left_plain) == [4, 5]


# --- Italic notes -----------------------------------------------------------

KNOWN = {"5.1.4", "5.1.5", "5.3.1", "6.1.1", "6.1.2"}


def test_a_labelled_note_citing_another_document_is_a_note():
    text = "Body.\n\n---\n\n*Data room note: correspondence under clause 9.4 is at 5.1.5.*\n"
    notes = italic_notes(text, "5.1.4", KNOWN)
    assert [(n.line, n.cites) for n in notes] == [(5, ("5.1.5",))]


def test_an_unlabelled_note_wrapped_over_lines_is_one_note():
    # 6.1.2's filing note walked the reader to the IP register and the Irish licence.
    text = (
        "Extract taken on 26 August 2026.\n\n"
        "*Filed on the trade mark papers. The register is at 6.1.1. The licence\n"
        "of this registration for the Irish market is at clause 2 of the distribution\n"
        "agreement at 5.3.1.*\n"
    )
    notes = italic_notes(text, "6.1.2", KNOWN)
    assert [(n.line, n.cites) for n in notes] == [(3, ("6.1.1", "5.3.1"))]


def test_underscore_italics_count_too():
    notes = italic_notes("_Related documents: the letter at 5.1.5._\n", "5.1.4", KNOWN)
    assert [n.cites for n in notes] == [("5.1.5",)]


@pytest.mark.parametrize(
    "text",
    [
        "*Reply:* the correspondence is at 5.1.5.\n",
        "*Filed with the contract papers at 5.1.4 and 9.9.9.*\n",
        "*Filed with the contract papers.*\n",
    ],
    ids=["italic-run-in-label", "own-and-unknown-slots-only", "no-index-number"],
)
def test_what_is_not_an_index_citing_note(text):
    assert italic_notes(text, "5.1.4", KNOWN) == []


# --- Dates and pointer direction --------------------------------------------


def test_a_dated_line_wins_over_an_earlier_date_in_the_header():
    text = "Reference to the agreement of 3 May 2020\n\nDated 1 September 2022\n\nBody."
    assert document_date(text) == (2022, 9, 1)


def test_document_date_reads_capitals_and_ordinals_and_gives_up_honestly():
    assert document_date("DATED 8 MARCH 2024\n") == (2024, 3, 8)
    assert document_date("Letter of 1st April 2021\n") == (2021, 4, 1)
    assert document_date("No date anywhere here.\n") is None
    assert document_date("\n".join(["filler"] * 30) + "\nDated 1 May 2020\n") is None


def _key(tmp_path, findings, distractors=()):
    key = tmp_path / "_key"
    key.mkdir(parents=True, exist_ok=True)
    (key / "findings.yaml").write_text(yaml.safe_dump({"room": "Project Testbed", "findings": findings}))
    (key / "distractors.yaml").write_text(yaml.safe_dump({"distractors": list(distractors)}))
    return load_findings(key / "findings.yaml"), load_distractors(key / "distractors.yaml")


def _pointers(tmp_path, documents, findings, distractors=()):
    for rel, text in documents.items():
        path = tmp_path / "data-room" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    roles = evidence_roles(*_key(tmp_path, findings, distractors))
    return {(p.rel.split("/")[-1][:5], p.target.split("/")[-1][:5]): p.direction
            for p in scan_room(tmp_path / "data-room", roles).pointers}


def note(slot):
    return f"\n\n---\n\n*Data room note: the related document is at {slot}.*\n"


def test_earlier_to_later_is_forward_and_later_to_earlier_is_backward(tmp_path):
    documents = {
        CONTRACT: "Dated 1 September 2022\n\nThe agreement." + note("5.1.5"),
        LETTER: "Date: 12 February 2026\n\nThe letter under clause 9.4." + note("5.1.4"),
    }
    assert _pointers(tmp_path, documents, [FINDING]) == {
        ("5.1.4", "5.1.5"): FORWARD,
        ("5.1.5", "5.1.4"): BACKWARD,
    }


def test_the_answer_key_root_does_not_decide_direction_when_the_dates_can(tmp_path):
    # Frithcombe's COMM-1 is planted in the ITT, so the ITT is the chain's root —
    # and its note to the framework agreement it was issued under is still backward.
    itt_rooted = dict(FINDING, source=LETTER, corroboration=[CONTRACT])
    documents = {
        CONTRACT: "Dated 1 February 2024\n\nThe framework agreement.",
        LETTER: "Date: 3 August 2026\n\nThe invitation to tender." + note("5.1.4"),
    }
    assert _pointers(tmp_path, documents, [itt_rooted]) == {("5.1.5", "5.1.4"): BACKWARD}


def test_same_day_documents_are_a_package_and_point_laterally(tmp_path):
    documents = {
        CONTRACT: "Dated 12 September 2019\n\nThe articles." + note("5.1.5"),
        LETTER: "Dated 12 September 2019\n\nThe resolution adopting them.",
    }
    assert _pointers(tmp_path, documents, [FINDING]) == {("5.1.4", "5.1.5"): LATERAL}


def test_a_distractor_points_forward_from_alarm_to_resolution_whatever_the_dates(tmp_path):
    documents = {
        ALARM: "Dated 1 April 2022\n\nThe haulage contract." + note("5.1.7"),
        WAIVER: "Dated 1 April 2022\n\nThe waiver letter." + note("5.1.6"),
    }
    assert _pointers(tmp_path, documents, [FINDING], [DISTRACTOR]) == {
        ("5.1.6", "5.1.7"): FORWARD,
        ("5.1.7", "5.1.6"): BACKWARD,
    }


def test_undated_documents_fall_back_to_the_chain_root(tmp_path):
    documents = {
        CONTRACT: "The agreement." + note("5.1.5"),
        LETTER: "The letter." + note("5.1.4"),
    }
    assert _pointers(tmp_path, documents, [FINDING]) == {
        ("5.1.4", "5.1.5"): FORWARD,
        ("5.1.5", "5.1.4"): BACKWARD,
    }


def test_a_note_to_a_document_outside_the_chain_is_not_a_pointer(tmp_path):
    documents = {
        CONTRACT: "Dated 1 September 2022\n\nThe agreement." + note("5.1.1"),
        LETTER: "Date: 12 February 2026\n\nThe letter.",
        OTHERS[0]: "The schedule of customer contracts.",
    }
    assert _pointers(tmp_path, documents, [FINDING]) == {}


# --- Note-presence asymmetry ------------------------------------------------


def test_asymmetry_counts_evidence_and_other_documents_per_section():
    has_note = {"12_insurance/a.md": True, "12_insurance/b.md": False, "13_pensions/c.md": True}
    sections = note_asymmetry(has_note, evidence={"12_insurance/a.md"})
    assert sections == [
        SectionNotes("12_insurance", 1, 1, 0, 1),
        SectionNotes("13_pensions", 0, 0, 1, 1),
    ]


def test_shares_are_floored_as_the_room_audit_reported_them():
    # Frithcombe section 12: 7 of 8 evidence documents against 3 of 16 others.
    assert SectionNotes("12_insurance", 7, 8, 3, 16).describe() == (
        "12_insurance notes on 87% of 8 evidence against 18% of 16 other"
    )


# --- The gate ---------------------------------------------------------------


def _room(tmp_path, documents, findings=(FINDING,), distractors=(DISTRACTOR,)):
    (tmp_path / "room.conf").write_text(CONF)
    for rel, text in documents.items():
        path = tmp_path / "data-room" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "data-room").mkdir(exist_ok=True)
    found, distracting = _key(tmp_path, list(findings), distractors)
    return GateContext(
        room=tmp_path, conf=load_room_conf(tmp_path / "room.conf"),
        findings=found, distractors=distracting,
    )


BOLD_CLAUSE = (
    "9.4 **The Supplier indemnifies Wexcott against all recall losses, and the "
    "indemnity is not subject to any financial cap or limit.**"
)
PLAIN_CLAUSE = BOLD_CLAUSE.replace("**", "")


def tells_room():
    """Every tell at once: the bold clause, a forward pointer from the contract
    to the letter and from the alarm to its waiver, and notes on three of four
    evidence documents against none of three others."""
    return {
        CONTRACT: "Dated 1 September 2022\n\n" + clauses(BOLD_CLAUSE) + note("5.1.5"),
        LETTER: "Date: 12 February 2026\n\nThe letter under clause 9.4." + note("5.1.4"),
        ALARM: "Dated 1 April 2022\n\nThe haulage contract." + note("5.1.7"),
        WAIVER: "Dated 1 April 2022\n\nThe waiver letter.",
        **{rel: "Dated 1 March 2023\n\n" + clauses() for rel in OTHERS},
    }


def fixed_room():
    """The Frithcombe fix, in miniature: the bold comes off, the forward
    pointers go, and — because removing notes outright made "no note" a tell of
    its own — every document gets an ordinary note to a benign sibling."""
    neutral = note("5.1.1")
    return {
        CONTRACT: "Dated 1 September 2022\n\n" + clauses(PLAIN_CLAUSE) + neutral,
        LETTER: "Date: 12 February 2026\n\nThe letter under clause 9.4." + note("5.1.4"),
        ALARM: "Dated 1 April 2022\n\nThe haulage contract." + neutral,
        WAIVER: "Dated 1 April 2022\n\nThe waiver letter." + neutral,
        OTHERS[0]: "Dated 1 March 2023\n\n" + clauses() + note("5.1.2"),
        **{rel: "Dated 1 March 2023\n\n" + clauses() + neutral for rel in OTHERS[1:]},
    }


def test_a_room_with_all_three_tells_warns_and_names_each(tmp_path):
    result = gate_20_tells(_room(tmp_path, tells_room()))
    assert (result.number, result.status) == ("20", "WARN")
    assert "selective bold on 1 line(s) in 1 evidence document(s): 5.1.4:9 COMM-1" in result.detail
    assert "2 forward pointer note(s): 5.1.4:" in result.detail and "(DX-1)" in result.detail
    assert "05_commercial notes on 75% of 4 evidence against 0% of 3 other" in result.detail
    assert "not counted: 1 backward" in result.detail


def test_the_fixed_room_passes_with_its_backward_pointer_reported(tmp_path):
    result = gate_20_tells(_room(tmp_path, fixed_room()))
    assert result.status == "PASS", result.detail
    assert "not counted: 1 backward, 0 same-date and 0 section-habit pointer(s)" in result.detail


def test_bold_in_a_document_outside_every_chain_is_not_the_gates_business(tmp_path):
    documents = fixed_room()
    documents[OTHERS[0]] = "Dated 1 March 2023\n\n" + clauses(BOLD_CLAUSE) + note("5.1.2")
    assert gate_20_tells(_room(tmp_path, documents)).status == "PASS"


def test_section_habits_are_the_documents_most_of_a_section_cites():
    cites = {
        "06_ip/a.md": ["6.1.1"],
        "06_ip/b.md": ["6.1.1", "6.2.1"],
        "06_ip/c.md": [],
        "06_ip/d.md": ["6.1.1"],
        "07_it/e.md": ["6.1.1"],
    }
    assert section_habits(cites, 0.5, 1) == {"06_ip": {"6.1.1"}, "07_it": {"6.1.1"}}
    assert section_habits(cites, 0.25, 1) == {"06_ip": {"6.1.1", "6.2.1"}, "07_it": {"6.1.1"}}
    # One document is the whole of section 07, but a habit of one is not a habit.
    assert section_habits(cites, 0.5, 3) == {"06_ip": {"6.1.1"}, "07_it": set()}


def test_a_forward_pointer_that_follows_the_sections_habit_is_not_counted(tmp_path):
    # Frithcombe's section 06 closes most documents with the IP register's index
    # number; the register is IP-2's evidence, so each copy read as a signpost.
    documents = tells_room()
    for rel in OTHERS:
        documents[rel] = "Dated 1 March 2023\n\n" + clauses() + note("5.1.5")
    result = gate_20_tells(_room(tmp_path, documents))
    assert "1 forward pointer note(s): 5.1.6:" in result.detail
    assert "0 same-date and 1 section-habit pointer(s)" in result.detail
    # Four of seven documents cite 5.1.5: a habit at half, not at three in five.
    stricter = gate_20_tells(_room(tmp_path, documents), Thresholds(hub_share=0.6))
    assert "2 forward pointer note(s)" in stricter.detail


def test_a_section_too_small_to_judge_is_not_judged(tmp_path):
    documents = tells_room()
    for rel in OTHERS[1:]:
        del documents[rel]
    result = gate_20_tells(_room(tmp_path, documents))
    assert "note asymmetry" not in result.detail
    assert "note asymmetry" in gate_20_tells(
        _room(tmp_path, documents), Thresholds(note_min_docs=1)
    ).detail


def test_skips_loudly_without_an_answer_key(tmp_path):
    result = gate_20_tells(_room(tmp_path, fixed_room(), findings=(), distractors=()))
    assert result.status == "SKIP"
    assert "no findings or distractors" in result.detail


def test_skips_loudly_without_a_blind_tree(tmp_path):
    result = gate_20_tells(_room(tmp_path, {}))
    assert result.status == "SKIP"
    assert "no markdown" in result.detail


def test_tells_warn_and_never_fail_the_run(tmp_path, capsys):
    """Warning-level by decision: a frozen room must not start failing --strict
    over a check it was never built against. Promoting it is REPORT's job."""
    assert REPORT is warn
    ctx = _room(tmp_path, tells_room())
    ctx.strict = True
    assert run_gates(ctx, [gate_20_tells]) == 0
    assert "1 warned" in capsys.readouterr().out


def test_gate_20_is_registered_last():
    assert ALL_GATES[-1] is gate_20_tells
    assert len(ALL_GATES) == 20


def test_the_xs_fixture_room_carries_no_tells(xs_room):
    ctx = GateContext(
        room=xs_room, conf=load_room_conf(xs_room / "room.conf"),
        findings=load_findings(xs_room / "_key" / "findings.yaml"),
        distractors=load_distractors(xs_room / "_key" / "distractors.yaml"),
    )
    assert gate_20_tells(ctx).status == "PASS"
