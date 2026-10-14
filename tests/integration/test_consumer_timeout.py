import pytest

from tests.integration.stack import ComposeStack, rabbitmqctl

pytestmark = pytest.mark.integration

# compose.yaml's default CONSUMER_TIMEOUT_MS: 5 minutes, rather than RabbitMQ's 30.
CONSUMER_TIMEOUT_MS = 300000


def test_rabbitmq_consumer_timeout_is_five_minutes(
    compose_stack: ComposeStack,
) -> None:
    configured = rabbitmqctl("--quiet", "eval", "application:get_env(rabbit, consumer_timeout).")

    assert configured.strip() == f"{{ok,{CONSUMER_TIMEOUT_MS}}}"
