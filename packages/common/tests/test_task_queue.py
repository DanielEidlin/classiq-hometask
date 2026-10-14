import json
import uuid

from common.task_queue import PROCESS_TASK_NAME, encode_task_message


def test_encodes_celery_protocol_v2_message() -> None:
    task_id = uuid.UUID("2f1c7a3e-0b6d-4c47-9a51-3d7e5f0c8b12")

    message = encode_task_message(task_id)

    assert message.headers["lang"] == "py"
    assert message.headers["task"] == PROCESS_TASK_NAME
    assert message.headers["id"] == str(task_id)
    assert message.headers["root_id"] == str(task_id)
    assert message.correlation_id == str(task_id)
    assert message.content_type == "application/json"
    assert message.content_encoding == "utf-8"

    args, kwargs, embed = json.loads(message.body)
    assert args == [str(task_id)]
    assert kwargs == {}
    assert embed == {"callbacks": None, "errbacks": None, "chain": None, "chord": None}


def test_message_is_persistent() -> None:
    message = encode_task_message(uuid.uuid4())

    assert message.delivery_mode == 2
