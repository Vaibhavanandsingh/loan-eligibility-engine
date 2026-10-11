import uuid

from src.handlers.process_csv import batch_id_for_key


def test_batch_id_comes_from_upload_key():
    bid = str(uuid.uuid4())
    assert batch_id_for_key("bucket", f"uploads/{bid}.csv") == bid


def test_other_keys_get_a_stable_id():
    first = batch_id_for_key("bucket", "uploads/my file.csv")
    assert first == batch_id_for_key("bucket", "uploads/my file.csv")
    assert first != batch_id_for_key("bucket", "uploads/other.csv")
    uuid.UUID(first)  # still a valid UUID
