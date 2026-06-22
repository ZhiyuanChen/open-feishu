import pytest

from feishu.ws._frame import (
    FRAME_TYPE_CONTROL,
    FRAME_TYPE_DATA,
    Frame,
    Header,
    decode_frame,
    encode_frame,
)


class TestWireFormat:
    def test_ping_bytes(self):
        # Hand-computed pbbp2.Frame encoding for a ping: SeqID=0, LogID=0, service=5,
        # method=0, headers=[{type: ping}]. This cross-checks the codec against the
        # protobuf wire format (not just self-consistency).
        ping = Frame(service=5, method=FRAME_TYPE_CONTROL, headers=[Header("type", "ping")])
        assert encode_frame(ping).hex() == "08001000180520002a0c0a0474797065120470696e67"

    def test_required_emitted_when_zero(self):
        # proto2 required SeqID/LogID/service/method must be present even at 0.
        raw = encode_frame(Frame())
        assert raw[:8].hex() == "0800100018002000"


class TestRoundTrip:
    def test_control(self):
        frame = Frame(service=5, method=FRAME_TYPE_CONTROL, headers=[Header("type", "pong")])
        assert decode_frame(encode_frame(frame)) == frame

    def test_data_with_payload(self):
        frame = Frame(
            seq_id=42,
            log_id=99,
            service=5,
            method=FRAME_TYPE_DATA,
            headers=[Header("type", "event"), Header("message_id", "m1")],
            payload_encoding="",
            payload_type="",
            payload=b'{"schema":"2.0"}',
        )
        assert decode_frame(encode_frame(frame)) == frame

    def test_fragment_headers(self):
        frame = Frame(
            method=FRAME_TYPE_DATA,
            headers=[Header("message_id", "m1"), Header("sum", "3"), Header("seq", "2")],
            payload=b"part",
        )
        back = decode_frame(encode_frame(frame))
        assert back.header("sum") == "3" and back.header("seq") == "2"

    def test_unicode(self):
        frame = Frame(headers=[Header("title", "你好")], payload="世界".encode())
        back = decode_frame(encode_frame(frame))
        assert back.header("title") == "你好" and back.payload == "世界".encode()

    def test_large_log_id(self):
        # uint64 values beyond 32 bits exercise multi-byte varints.
        frame = Frame(seq_id=2**53 + 1, log_id=2**63 - 1)
        assert decode_frame(encode_frame(frame)).log_id == 2**63 - 1


class TestRobustness:
    @pytest.mark.parametrize(
        "trailing",
        [
            bytes([(15 << 3) | 0]) + b"\x01",  # future varint field
            bytes([(20 << 3) | 2]) + b"\x03abc",  # future length-delimited field
        ],
    )
    def test_unknown_field_skipped(self, trailing):
        # Forward compatibility: unknown future fields must be ignored.
        raw = encode_frame(Frame(headers=[Header("type", "event")])) + trailing
        assert decode_frame(raw).header("type") == "event"

    def test_truncated_varint_raises(self):
        with pytest.raises(ValueError):
            decode_frame(b"\x08\xff")  # field 1 tag + unterminated varint

    def test_header_missing_returns_none(self):
        assert Frame(headers=[Header("type", "event")]).header("absent") is None

    def test_optional_fields_omitted_when_none(self):
        # No payload/encoding fields emitted when unset; decode leaves them None.
        back = decode_frame(encode_frame(Frame(method=1)))
        assert back.payload is None and back.payload_encoding is None and back.log_id_new is None
