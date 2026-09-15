import hashlib
import hmac

from app.infra.interfaces.mercadopago_signature import is_valid_signature

SECRET = "mercadopago-secret"


def sign(manifest: str) -> str:
    return hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_matches_documented_manifest():
    v1 = sign("id:123456;request-id:req-1;ts:1704908010;")

    assert is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="123456")


def test_alphanumeric_data_id_is_lowercased_in_manifest():
    v1 = sign("id:ord01jq4s4ky8hwq6na5pxb65b3d3;request-id:req-1;ts:1704908010;")

    assert is_valid_signature(
        secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="ORD01JQ4S4KY8HWQ6NA5PXB65B3D3"
    )


def test_missing_request_id_is_removed_from_manifest():
    v1 = sign("id:123456;ts:1704908010;")

    assert is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010, v1={v1}", request_id=None, data_id="123456")


def test_tampered_or_missing_signature_is_rejected():
    v1 = sign("id:123456;request-id:req-1;ts:1704908010;")

    assert not is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="999")
    assert not is_valid_signature(secret=SECRET, signature_header=None, request_id="req-1", data_id="123456")
    assert not is_valid_signature(secret=SECRET, signature_header="v1=abc", request_id="req-1", data_id="123456")
    assert not is_valid_signature(secret="", signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="123456")
