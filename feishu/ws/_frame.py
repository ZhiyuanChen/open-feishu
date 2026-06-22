# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

r"""
飞书长连接（WebSocket）帧的最小化 Protobuf 编解码。

飞书长连接在 WebSocket 之上承载一层自定义的 Protobuf 帧（`pbbp2.Frame`）。其结构十分简单，
仅用到 varint 与 length-delimited 两种 wire type，因此本模块手写编解码，避免引入 `protobuf`
运行时依赖与生成代码。帧的定义（proto2）等价于：

```proto
message Header { required string key = 1; required string value = 2; }
message Frame {
  required uint64 SeqID = 1;
  required uint64 LogID = 2;
  required int32 service = 3;
  required int32 method = 4;
  repeated Header headers = 5;
  optional string payload_encoding = 6;
  optional string payload_type = 7;
  optional bytes payload = 8;
  optional string LogIDNew = 9;
}
```

解码时对未知字段（其它 field number 或 wire type）按 wire type 跳过，以兼容协议演进。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Frame.method 取值：控制帧（心跳）与数据帧（事件/卡片）。
FRAME_TYPE_CONTROL = 0
FRAME_TYPE_DATA = 1


@dataclass
class Header:
    r"""
    帧头部的一个键值对（`pbbp2.Header`）。

    Args:
        key: 头部键，如 `type`、`message_id`、`sum`、`seq`、`trace_id`、`biz_rt`。
        value: 头部值，均以字符串承载。
    """

    key: str
    value: str


@dataclass
class Frame:
    r"""
    飞书长连接的一帧（`pbbp2.Frame`）。

    Args:
        seq_id: 帧序号（`SeqID`）。
        log_id: 日志 ID（`LogID`）。
        service: 服务编号，由握手返回的 WebSocket URL 中的 `service_id` 决定。
        method: 帧类型，`0` 为控制帧（心跳），`1` 为数据帧（事件/卡片）。
        headers: 头部键值对列表。
        payload_encoding: 负载编码（可选）。
        payload_type: 负载类型（可选）。
        payload: 负载字节，数据帧中通常为一段 UTF-8 的事件 JSON（可选）。
        log_id_new: 新版日志 ID（`LogIDNew`，可选）。
    """

    seq_id: int = 0
    log_id: int = 0
    service: int = 0
    method: int = 0
    headers: list[Header] = field(default_factory=list)
    payload_encoding: str | None = None
    payload_type: str | None = None
    payload: bytes | None = None
    log_id_new: str | None = None

    def header(self, key: str) -> str | None:
        r"""
        按键读取首个匹配的头部值，缺失时返回 `None`。

        Args:
            key: 头部键。

        Returns:
            匹配到的头部值；不存在时返回 `None`。

        Examples:
            >>> Frame(headers=[Header("type", "event")]).header("type")
            'event'
            >>> Frame().header("type") is None
            True
        """
        for item in self.headers:
            if item.key == key:
                return item.value
        return None


def _encode_varint(value: int) -> bytes:
    r"""
    将非负整数编码为 Protobuf base-128 varint。

    Args:
        value: 待编码的非负整数。

    Returns:
        varint 字节序列。

    Examples:
        >>> _encode_varint(0)
        b'\x00'
        >>> _encode_varint(300)
        b'\xac\x02'
    """
    out = bytearray()
    n = value
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _decode_varint(data: bytes, index: int) -> tuple[int, int]:
    r"""
    从 `data[index:]` 解码一个 varint。

    Args:
        data: 源字节。
        index: 起始下标。

    Returns:
        `(值, 新下标)` 二元组。

    Raises:
        ValueError: 当 varint 在数据耗尽前未结束时抛出。

    Examples:
        >>> _decode_varint(b'\xac\x02', 0)
        (300, 2)
    """
    result = 0
    shift = 0
    while index < len(data):
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
    raise ValueError("truncated varint in frame")


def _tag(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _length_delimited(field_number: int, data: bytes) -> bytes:
    return _tag(field_number, 2) + _encode_varint(len(data)) + data


def _encode_header(header: Header) -> bytes:
    return _length_delimited(1, header.key.encode("utf-8")) + _length_delimited(2, header.value.encode("utf-8"))


def encode_frame(frame: Frame) -> bytes:
    r"""
    将 [Frame][feishu.ws._frame.Frame] 编码为 `pbbp2.Frame` 字节序列。

    四个 proto2 required 字段（SeqID/LogID/service/method）总是写出（即使为 0）；其余可选字段
    仅在非 `None` 时写出。

    Args:
        frame: 待编码的帧。

    Returns:
        Protobuf 编码后的字节序列。

    Examples:
        >>> frame = Frame(service=5, method=FRAME_TYPE_CONTROL, headers=[Header("type", "ping")])
        >>> encode_frame(frame).hex()
        '08001000180520002a0c0a0474797065120470696e67'
    """
    out = bytearray()
    out += _tag(1, 0) + _encode_varint(frame.seq_id)
    out += _tag(2, 0) + _encode_varint(frame.log_id)
    out += _tag(3, 0) + _encode_varint(frame.service)
    out += _tag(4, 0) + _encode_varint(frame.method)
    for header in frame.headers:
        out += _length_delimited(5, _encode_header(header))
    if frame.payload_encoding is not None:
        out += _length_delimited(6, frame.payload_encoding.encode("utf-8"))
    if frame.payload_type is not None:
        out += _length_delimited(7, frame.payload_type.encode("utf-8"))
    if frame.payload is not None:
        out += _length_delimited(8, frame.payload)
    if frame.log_id_new is not None:
        out += _length_delimited(9, frame.log_id_new.encode("utf-8"))
    return bytes(out)


def _skip_field(data: bytes, index: int, wire_type: int) -> int:
    r"""跳过一个未知字段，返回新下标。"""
    if wire_type == 0:
        _, index = _decode_varint(data, index)
        return index
    if wire_type == 2:
        length, index = _decode_varint(data, index)
        return index + length
    if wire_type == 1:  # 64-bit
        return index + 8
    if wire_type == 5:  # 32-bit
        return index + 4
    raise ValueError(f"unsupported wire type {wire_type} in frame")


def _decode_header(data: bytes) -> Header:
    key = ""
    value = ""
    index = 0
    while index < len(data):
        tag, index = _decode_varint(data, index)
        field_number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 2 and field_number in (1, 2):
            length, index = _decode_varint(data, index)
            chunk = data[index : index + length]
            index += length
            if field_number == 1:
                key = chunk.decode("utf-8")
            else:
                value = chunk.decode("utf-8")
        else:
            index = _skip_field(data, index, wire_type)
    return Header(key, value)


def decode_frame(data: bytes) -> Frame:
    r"""
    将 `pbbp2.Frame` 字节序列解码为 [Frame][feishu.ws._frame.Frame]。

    未知的 field number 或 wire type 会被安全跳过，以兼容协议演进。

    Args:
        data: Protobuf 编码的帧字节。

    Returns:
        解码出的帧。

    Raises:
        ValueError: 当字节序列结构非法（如截断的 varint 或不支持的 wire type）时抛出。

    Examples:
        >>> frame = Frame(seq_id=7, service=5, method=1, headers=[Header("type", "event")], payload=b'{}')
        >>> back = decode_frame(encode_frame(frame))
        >>> back.seq_id, back.service, back.method, back.header("type"), back.payload
        (7, 5, 1, 'event', b'{}')
    """
    frame = Frame()
    index = 0
    while index < len(data):
        tag, index = _decode_varint(data, index)
        field_number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 0:
            value, index = _decode_varint(data, index)
            if field_number == 1:
                frame.seq_id = value
            elif field_number == 2:
                frame.log_id = value
            elif field_number == 3:
                frame.service = value
            elif field_number == 4:
                frame.method = value
        elif wire_type == 2:
            length, index = _decode_varint(data, index)
            chunk = data[index : index + length]
            index += length
            if field_number == 5:
                frame.headers.append(_decode_header(chunk))
            elif field_number == 6:
                frame.payload_encoding = chunk.decode("utf-8")
            elif field_number == 7:
                frame.payload_type = chunk.decode("utf-8")
            elif field_number == 8:
                frame.payload = bytes(chunk)
            elif field_number == 9:
                frame.log_id_new = chunk.decode("utf-8")
        else:
            index = _skip_field(data, index, wire_type)
    return frame
