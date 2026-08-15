"""Optional RabbitMQ transport; imported only when configured."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import logging
import os
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)


class RabbitMQUnavailable(RuntimeError):
    pass


class RabbitMQ:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("RABBITMQ_URL", "").strip()
        if not self.url:
            raise RabbitMQUnavailable("RABBITMQ_URL is not configured")
        try:
            import pika
        except ImportError as error:
            raise RabbitMQUnavailable("Install pika to use RabbitMQ") from error
        self._pika = pika
        self.connection = pika.BlockingConnection(pika.URLParameters(self.url))
        self.channel = self.connection.channel()
        self._publish_connection = None
        self._publish_channel = None

    def _close_publisher(self) -> None:
        connection = self._publish_connection
        self._publish_channel = None
        self._publish_connection = None
        if connection is not None and connection.is_open:
            connection.close()

    def _publisher(self):
        if (self._publish_connection is None or self._publish_connection.is_closed
                or self._publish_channel is None or self._publish_channel.is_closed):
            self._close_publisher()
            self._publish_connection = self._pika.BlockingConnection(self._pika.URLParameters(self.url))
            self._publish_channel = self._publish_connection.channel()
        return self._publish_channel

    def publish(self, queue: str, event: dict[str, Any]) -> None:
        data = json.dumps(event, ensure_ascii=False).encode("utf-8")
        recoverable = (self._pika.exceptions.AMQPConnectionError,
                       self._pika.exceptions.ConnectionWrongStateError,
                       self._pika.exceptions.ChannelWrongStateError)
        for attempt in range(2):
            try:
                channel = self._publisher()
                channel.queue_declare(queue=queue, durable=True)
                channel.basic_publish(exchange="", routing_key=queue, body=data,
                    properties=self._pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                    mandatory=True)
                return
            except recoverable:
                self._close_publisher()
                if attempt == 1:
                    raise
                LOGGER.warning("RabbitMQ publish connection lost; reconnecting")

    def consume(self, queue: str, handler: Callable[[dict[str, Any]], None], *,
                worker_start: Callable[[], None] | None = None,
                worker_stop: Callable[[], None] | None = None) -> None:
        self.consume_many([queue], handler, worker_start=worker_start, worker_stop=worker_stop)

    def consume_many(self, queues: list[str], handler: Callable[[dict[str, Any]], None], *,
                     worker_start: Callable[[], None] | None = None,
                     worker_stop: Callable[[], None] | None = None) -> None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rabbitmq-handler")

        def settle(delivery_tag: int, succeeded: bool) -> None:
            if not self.channel.is_open:
                return
            if succeeded:
                self.channel.basic_ack(delivery_tag)
            else:
                self.channel.basic_nack(delivery_tag, requeue=True)

        def completed(delivery_tag: int, future: Future[None]) -> None:
            error = future.exception()
            if error is not None:
                LOGGER.exception("RabbitMQ message handler failed", exc_info=error)
            try:
                self.connection.add_callback_threadsafe(
                    lambda: settle(delivery_tag, error is None))
            except (self._pika.exceptions.AMQPConnectionError,
                    self._pika.exceptions.ConnectionWrongStateError):
                LOGGER.warning("RabbitMQ connection closed before message could be acknowledged")

        def callback(channel, method, properties, body):
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                channel.basic_nack(method.delivery_tag, requeue=True)
                return
            future = executor.submit(handler, payload)
            future.add_done_callback(lambda result: completed(method.delivery_tag, result))

        try:
            if worker_start is not None:
                executor.submit(worker_start).result()
            self.channel.basic_qos(prefetch_count=1)
            for queue in queues:
                self.channel.queue_declare(queue=queue, durable=True)
                self.channel.basic_consume(queue=queue, on_message_callback=callback)
            self.channel.start_consuming()
        finally:
            executor.submit(self._close_publisher).result()
            if worker_stop is not None:
                executor.submit(worker_stop).result()
            executor.shutdown(wait=True)

    def close(self) -> None:
        self._close_publisher()
        if getattr(self, "connection", None) and self.connection.is_open:
            self.connection.close()
