"""Regression coverage for RCA-shaped multi-table reconciliation.

The RCA template combines heading paragraphs, person mentions, rewritten
instructional paragraphs, and timeline/evidence tables.  Inserting more than
one table in the same body update used to leave later paragraph requests at
their pre-table indices, which garbled text near the Action Plan.
"""

from __future__ import annotations

import copy

from extradoc.api_types._generated import (
    InsertTableRequest,
    InsertTextRequest,
    Location,
    Paragraph,
    ParagraphElement,
    ParagraphStyle,
    Person,
    PersonProperties,
    StructuralElement,
    TextRun,
    Request,
)
from extradoc.diffmerge.model import UpdateBodyContentOp
from extradoc.reconcile_v3.api import diff, reconcile_batches
from extradoc.reconcile_v3.lower import _body_insert_events_for_table_batch

from tests.reconcile_v3.helpers import (
    make_indexed_doc,
    make_indexed_para,
    make_indexed_terminal,
    make_para_el,
    make_table_el,
)


def _person_para(start: int) -> StructuralElement:
    return StructuralElement(
        start_index=start,
        end_index=start + 2,
        paragraph=Paragraph(
            elements=[
                ParagraphElement(
                    person=Person(
                        person_properties=PersonProperties(email="owner@example.com")
                    )
                ),
                ParagraphElement(text_run=TextRun(content="\n")),
            ],
            paragraph_style=ParagraphStyle(named_style_type="NORMAL_TEXT"),
        ),
    )


def _text(element: StructuralElement) -> str:
    if element.paragraph is None:
        return "<TABLE>"
    return "".join(
        e.text_run.content
        for e in element.paragraph.elements or []
        if e.text_run is not None
    )


def test_rca_action_plan_reconciliation_preserves_structure_after_tables() -> None:
    cursor = 1
    root = make_indexed_para("Root Cause Analysis\n", cursor, "HEADING_1")
    cursor = root.end_index or cursor
    owner = _person_para(cursor)
    cursor = owner.end_index or cursor
    action_heading = make_indexed_para("Action Plan\n", cursor, "HEADING_2")
    cursor = action_heading.end_index or cursor
    instructions = make_indexed_para(
        "Describe the required actions for customers.\n", cursor, "NORMAL_TEXT"
    )
    cursor = instructions.end_index or cursor
    anchor = make_indexed_para("Timeline anchor.\n", cursor, "NORMAL_TEXT")
    cursor = anchor.end_index or cursor
    validation = make_indexed_para("Validation is pending.\n", cursor, "HEADING_2")
    cursor = validation.end_index or cursor
    terminal = make_indexed_terminal(cursor)

    base = make_indexed_doc(
        body_content=[
            root,
            owner,
            action_heading,
            instructions,
            anchor,
            validation,
            terminal,
        ]
    )

    desired = copy.deepcopy(base)
    body = desired.tabs[0].document_tab.body.content  # type: ignore[union-attr]
    body[3] = make_para_el(
        "The required actions for customers are complete.\n", "NORMAL_TEXT"
    )
    body[5] = make_para_el("Validation passed.\n", "HEADING_2")
    body[4:4] = [make_table_el([["Date", "Event"], ["2026-09-10", "Early signal"]])]
    body[6:6] = [make_table_el([["Resolved", "Yes"]])]
    assert _text(body[3]) == "The required actions for customers are complete.\n"
    assert any(_text(element) == "Validation passed.\n" for element in body)

    batches = reconcile_batches(base, desired)
    requests = [request for batch in batches for request in batch.requests or []]

    # Each table must be isolated so its structural index delta cannot poison
    # later paragraph requests.
    assert sum(request.insert_table is not None for request in requests) == 2
    assert all(
        sum(request.insert_table is not None for request in batch.requests or []) <= 1
        for batch in batches
    )

    body_diff = next(
        op for op in diff(base, desired) if isinstance(op, UpdateBodyContentOp)
    )
    alignment = body_diff.alignment
    assert any(
        _text(body_diff.base_content[match.base_idx]) == "Action Plan\n"
        and _text(body_diff.desired_content[match.desired_idx]) == "Action Plan\n"
        for match in alignment.matches
    )
    assert any(
        element.paragraph is not None
        and element.paragraph.elements
        and element.paragraph.elements[0].person is not None
        and element.paragraph.elements[0].person.person_properties is not None
        and element.paragraph.elements[0].person.person_properties.email
        == "owner@example.com"
        for element in body_diff.base_content
    )
    assert action_heading.paragraph is not None
    assert action_heading.paragraph.paragraph_style is not None
    assert (
        action_heading.paragraph.paragraph_style.named_style_type == "HEADING_2"
    )

    # The owner mention and Action Plan heading are preserved anchors: no
    # whole-element delete may target either of their base ranges.
    protected_ranges = {
        (owner.start_index, owner.end_index),
        (action_heading.start_index, action_heading.end_index),
    }
    assert not any(
        request.delete_content_range is not None
        and (
            request.delete_content_range.range.start_index,
            request.delete_content_range.range.end_index,
        )
        in protected_ranges
        for request in requests
    )

def test_table_batch_rebase_uses_live_delta_and_body_scope() -> None:
    table = Request(
        insert_table=InsertTableRequest(
            rows=1,
            columns=1,
            location=Location(index=10, tab_id="t1"),
        )
    )
    cell_fill = Request(
        insert_text=InsertTextRequest(
            location=Location(index=14, tab_id="t1"),
            text="😀",
        )
    )
    header_table = Request(
        insert_table=InsertTableRequest(
            rows=1,
            columns=1,
            location=Location(index=10, tab_id="t1", segment_id="h1"),
        )
    )

    assert _body_insert_events_for_table_batch([table, cell_fill]) == [(10, 1, "t1")]
    assert _body_insert_events_for_table_batch([header_table]) == []
