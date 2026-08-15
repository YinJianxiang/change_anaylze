import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from orchestrator.messaging.rabbitmq import RabbitMQ


class RecoverableConnectionError(Exception):
    pass


class WrongConnectionStateError(Exception):
    pass


class WrongChannelStateError(Exception):
    pass


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel
        self.is_open = True
        self.is_closed = False

    def channel(self):
        return self._channel

    def close(self):
        self.is_open = False
        self.is_closed = True


def fake_pika(connections=None):
    available = iter(connections or [])
    return SimpleNamespace(
        BlockingConnection=lambda parameters: next(available),
        URLParameters=lambda url: url,
        BasicProperties=lambda **values: values,
        exceptions=SimpleNamespace(
            AMQPConnectionError=RecoverableConnectionError,
            ConnectionWrongStateError=WrongConnectionStateError,
            ChannelWrongStateError=WrongChannelStateError,
        ),
    )


class RabbitMQTests(unittest.TestCase):
    def make_broker(self, pika):
        broker = RabbitMQ.__new__(RabbitMQ)
        broker.url = "amqp://localhost"
        broker._pika = pika
        broker._publish_connection = None
        broker._publish_channel = None
        return broker

    def test_publish_reconnects_after_connection_loss(self):
        failed_channel = Mock()
        failed_channel.is_closed = False
        failed_channel.queue_declare.side_effect = RecoverableConnectionError("lost")
        healthy_channel = Mock()
        healthy_channel.is_closed = False
        pika = fake_pika([FakeConnection(failed_channel), FakeConnection(healthy_channel)])
        broker = self.make_broker(pika)

        broker.publish("analysis.completed", {"event_id": "evt-1"})

        healthy_channel.basic_publish.assert_called_once()
        self.assertEqual(healthy_channel.basic_publish.call_args.kwargs["routing_key"], "analysis.completed")

    def test_slow_handler_does_not_block_consumer_callback(self):
        release_handler = threading.Event()
        callback_scheduled = threading.Event()
        scheduled = []
        worker_threads = []

        class ConsumerConnection:
            is_open = True

            def add_callback_threadsafe(self, callback):
                scheduled.append(callback)
                callback_scheduled.set()

        class ConsumerChannel:
            is_open = True

            def basic_qos(self, **kwargs):
                pass

            def queue_declare(self, **kwargs):
                pass

            def basic_consume(self, **kwargs):
                self.callback = kwargs["on_message_callback"]

            def start_consuming(self):
                started = time.monotonic()
                self.callback(self, SimpleNamespace(delivery_tag=7), None, b'{"task_id":"1"}')
                self.callback_elapsed = time.monotonic() - started
                release_handler.set()
                self.assertion = callback_scheduled.wait(1)

            def basic_ack(self, delivery_tag):
                self.acked = delivery_tag

            def basic_nack(self, delivery_tag, requeue):
                self.nacked = (delivery_tag, requeue)

        channel = ConsumerChannel()
        broker = self.make_broker(fake_pika())
        broker.connection = ConsumerConnection()
        broker.channel = channel

        def start_worker():
            worker_threads.append(("start", threading.get_ident()))

        def handle(event):
            worker_threads.append(("handle", threading.get_ident()))
            release_handler.wait(1)

        def stop_worker():
            worker_threads.append(("stop", threading.get_ident()))

        broker.consume("mail.task.created", handle,
                       worker_start=start_worker, worker_stop=stop_worker)
        for callback in scheduled:
            callback()

        self.assertLess(channel.callback_elapsed, 0.1)
        self.assertTrue(channel.assertion)
        self.assertEqual(channel.acked, 7)
        self.assertEqual([name for name, _ in worker_threads], ["start", "handle", "stop"])
        self.assertEqual(len({thread_id for _, thread_id in worker_threads}), 1)


if __name__ == "__main__":
    unittest.main()
