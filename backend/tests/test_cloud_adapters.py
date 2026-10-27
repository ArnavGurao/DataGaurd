from __future__ import annotations

import uuid

from app.models import Job, NotificationEvent
from app.services.dispatcher import QueueMessage, SqsJobQueue
from app.services.notifier import SnsNotifier
from app.services.storage import S3Storage


class FakeBody:
    def read(self) -> bytes:
        return b"id,name\n1,Ada\n"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": FakeBody()}

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))

    def generate_presigned_url(self, operation, **kwargs):
        self.calls.append(("generate_presigned_url", {"operation": operation, **kwargs}))
        return "https://example.test/download"

    def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))

    def receive_message(self, **kwargs):
        self.calls.append(("receive_message", kwargs))
        return {
            "Messages": [
                {
                    "Body": str(TEST_JOB_ID),
                    "ReceiptHandle": "receipt",
                    "Attributes": {"ApproximateReceiveCount": "2"},
                },
                {"Body": "not-a-uuid", "ReceiptHandle": "bad"},
            ]
        }

    def delete_message(self, **kwargs):
        self.calls.append(("delete_message", kwargs))

    def publish(self, **kwargs):
        self.calls.append(("publish", kwargs))


TEST_JOB_ID = uuid.uuid4()


def test_s3_adapter_uses_bucket_and_preserves_download_filename(monkeypatch):
    from app.services import storage as module

    client = FakeClient()
    monkeypatch.setattr(module.S3Storage, "_client", lambda self: client)
    storage = S3Storage(bucket="dataguard-inputs-2026", region="us-east-1")

    storage.save_bytes("uploads/u/j/original.csv", b"data")
    assert storage.read_bytes("uploads/u/j/original.csv") == b"id,name\n1,Ada\n"
    assert storage.exists("uploads/u/j/original.csv")
    assert storage.get_download_url("uploads/u/j/original.csv", "unsafe/name.csv", 60)
    storage.delete_object("uploads/u/j/original.csv")

    assert [name for name, _ in client.calls] == [
        "put_object",
        "get_object",
        "head_object",
        "generate_presigned_url",
        "delete_object",
    ]
    presign = client.calls[3][1]
    assert client.calls[0][1]["ContentType"] == "text/csv"
    assert presign["Params"]["ResponseContentDisposition"] == 'attachment; filename="name.csv"'


def test_sqs_adapter_validates_messages_and_acknowledges(monkeypatch):
    from app.services import dispatcher as module

    client = FakeClient()
    monkeypatch.setattr(module.SqsJobQueue, "_client", lambda self: client)
    queue = SqsJobQueue(queue_url="https://sqs.us-east-1.amazonaws.com/905838125397/dataguard-jobs")

    queue.send(TEST_JOB_ID)
    messages = queue.receive(wait_seconds=20, max_messages=1)
    assert messages == [QueueMessage(TEST_JOB_ID, "receipt", 2)]
    queue.delete(messages[0])

    assert client.calls[0][0] == "send_message"
    assert client.calls[1][1]["WaitTimeSeconds"] == 20
    assert client.calls[2][1]["ReceiptHandle"] == "receipt"


def test_sns_adapter_publishes_identifiable_operator_notice(monkeypatch):
    from app.services import notifier as module

    client = FakeClient()
    monkeypatch.setattr(module.SnsNotifier, "_client", lambda self: client)
    notifier = SnsNotifier(topic_arn="arn:aws:sns:us-east-1:905838125397:dataguard-operators")
    event = NotificationEvent(id=uuid.uuid4(), event_type="JOB_FAILED", job_id=TEST_JOB_ID)
    job = Job(id=TEST_JOB_ID, status="FAILED", attempts=3)

    notifier.send(event, job)

    publish = client.calls[0][1]
    assert str(event.id) in publish["Message"]
    assert str(TEST_JOB_ID) in publish["Message"]
    assert publish["TopicArn"].endswith("dataguard-operators")
