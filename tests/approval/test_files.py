from feishu.approval import (
    approval_file_code,
    approval_file_type_for_media_type,
    normalize_approval_file_upload_response,
)


def test_approval_file_code_reads_direct_code():
    assert approval_file_code({"code": "file_1"}) == "file_1"


def test_approval_file_code_reads_urls_detail_code():
    response = {"urls_detail": [{"code": ""}, {"code": "file_2"}]}

    assert approval_file_code(response) == "file_2"


def test_normalize_upload_response_keeps_context():
    normalized = normalize_approval_file_upload_response(
        {"urls_detail": [{"code": "file_3"}]},
        file_type="image",
        media_type="image/png",
    )

    assert normalized.status == "uploaded"
    assert normalized.code == "file_3"
    assert normalized.file_type == "image"
    assert normalized.media_type == "image/png"
    assert approval_file_type_for_media_type("image/png") == "image"
    assert approval_file_type_for_media_type("application/pdf") == "attachment"


def test_normalize_upload_response_reports_missing_code():
    normalized = normalize_approval_file_upload_response({})

    assert normalized.status == "upload_failed"
    assert "did not return code" in normalized.error
