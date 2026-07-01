from feishu.drive import (
    document_reference_from_mapping,
    meeting_note_reference_from_mapping,
    meeting_note_reference_from_meeting,
)


def test_document_reference_from_mapping_prefers_token_fields():
    reference = document_reference_from_mapping({"document_token": "doxcn123456789", "doc_type": "docx"})

    assert reference is not None
    assert reference.token == "doxcn123456789"
    assert reference.doc_type == "docx"


def test_document_reference_from_mapping_falls_back_to_text_url():
    reference = document_reference_from_mapping({}, text="see https://example.feishu.cn/wiki/wikcn123456789")

    assert reference is not None
    assert reference.token == "wikcn123456789"
    assert reference.doc_type == "wiki"


def test_meeting_note_reference_from_mapping_reads_note_token():
    reference = meeting_note_reference_from_mapping({"meeting_note_token": "doxcn123456789"})

    assert reference is not None
    assert reference.token == "doxcn123456789"
    assert reference.doc_type == "docx"


def test_meeting_note_reference_from_meeting_reads_note_url():
    meeting = {"note_url": "https://example.feishu.cn/docx/doxcn123456789"}

    reference = meeting_note_reference_from_meeting(meeting)

    assert reference is not None
    assert reference.token == "doxcn123456789"
    assert reference.doc_type == "docx"
