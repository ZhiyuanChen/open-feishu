import base64
import hashlib

import pytest

from feishu.errors import FeishuCryptoError
from feishu.events.crypto import decrypt


class TestDecrypt:
    def test_official_test_vector(self):
        # Official Feishu doc vector: key "test key" -> "hello world".
        assert decrypt("test key", "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk=") == b"hello world"

    def test_roundtrip_with_pkcs7_padding(self):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        encrypt_key = "ek_secret"
        plaintext = b'{"schema":"2.0","header":{"event_type":"im.message.receive_v1"}}'
        key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        iv = b"\x00" * 16
        ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext, AES.block_size))
        blob = base64.b64encode(iv + ct).decode("ascii")
        assert decrypt(encrypt_key, blob) == plaintext

    @pytest.mark.parametrize(
        "blob",
        [
            base64.b64encode(b"\x00" * 32).decode("ascii"),  # bad PKCS7 padding
            "!!!not base64!!!",  # invalid base64
        ],
    )
    def test_invalid_blob_raises(self, blob):
        with pytest.raises(FeishuCryptoError):
            decrypt("test key", blob)

    def test_short_ciphertext_raises(self):
        # Fewer than one AES block of decoded bytes is rejected.
        short = base64.b64encode(b"\x00" * 8).decode("ascii")
        with pytest.raises(FeishuCryptoError, match="AES block"):
            decrypt("test key", short)
