import os
import threading

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker

from task_server.api_testing import http, tasks


def test_explicit_producer_targets_versioned_task_and_queue(monkeypatch):
    captured = []
    monkeypatch.setattr(tasks.generate_api_cases_thinking_v1, 'apply_async', lambda **kw: captured.append(kw))
    http._enqueue_thinking_ai_job('job')
    assert tasks.generate_api_cases_thinking_v1.name == 'api_testing.generate_cases_thinking_v1'
    assert captured == [{'args': ['job'], 'queue': 'api-testing-thinking-v1', 'expires': 300}]


def test_real_broker_keeps_explicit_message_for_new_queue_only(monkeypatch):
    broker = os.getenv('TEST_REDIS_URL')
    if not broker:
        pytest.skip('isolated TEST_REDIS_URL is required')
    import redis
    client = redis.Redis.from_url(broker)
    old_queue = 'api-thinking-test-old'
    new_queue = 'api-thinking-test-v1'
    client.delete(old_queue, new_queue)
    received_new = threading.Event()
    old_marker = threading.Event()
    old = Celery('old-isolated', broker=broker)
    new = Celery('new-isolated', broker=broker)
    old.conf.task_default_queue = old_queue
    new.conf.task_default_queue = new_queue

    @old.task(name='test.legacy.marker')
    def legacy(_job):
        old_marker.set()

    class FakeAiService:
        def __init__(self, _factory): pass
        def process(self, _job, *, generation_contract=None):
            assert generation_contract == 'thinking-v1'
            received_new.set()
            return type('Result', (), {'state': 'completed'})()
    class FakeTaskService:
        def __init__(self, _factory): pass
        def refresh_for_ai_job(self, _job, *, expected_designing_only=False):
            assert expected_designing_only is True
    monkeypatch.setattr(tasks, '_session_factory', lambda: object())
    monkeypatch.setattr(tasks, 'AiCaseService', FakeAiService)
    monkeypatch.setattr(tasks, 'TestTaskService', FakeTaskService)

    try:
        with start_worker(old, pool='solo', queues=[old_queue], perform_ping_check=False):
            new.send_task(tasks.generate_api_cases_thinking_v1.name, args=['fixture'], queue=new_queue)
            old.send_task(legacy.name, args=['fixture'], queue=old_queue)
            assert old_marker.wait(5), 'old worker was not actually consuming its queue'
            assert not received_new.is_set()
            assert client.llen(new_queue) == 1
        with start_worker(new, pool='solo', queues=[new_queue], perform_ping_check=False):
            assert received_new.wait(5), 'versioned worker did not consume its queue'
    finally:
        client.delete(old_queue, new_queue)
