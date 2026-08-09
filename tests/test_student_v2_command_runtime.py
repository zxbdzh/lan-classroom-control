import threading
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

from student.core.v2.command import (
    Command,
    CommandExecutor,
    OutcomePhase,
    OutcomeStatus,
)
from student.core.v2.ipc import ChannelClosedError, create_memory_channel_pair
from student.core.v2.ipc import WindowsNamedPipeConnector, WindowsNamedPipeListener
from student.core.v2.runtime import (
    SessionAgentProxy,
    SessionAgentRuntime,
    StudentServiceRuntime,
)


def test_frozen_service_entrypoint_dispatches_to_scm_without_cli_arguments():
    import student.service as service

    manager = MagicMock()
    with patch.object(service, "servicemanager", manager), patch.object(
        service, "win32serviceutil", MagicMock()
    ), patch.object(sys, "argv", [service.__file__]):
        service.main()

    manager.Initialize.assert_called_once_with()
    manager.PrepareToHostSingle.assert_called_once_with(
        service.LanClassroomStudentService
    )
    manager.StartServiceCtrlDispatcher.assert_called_once_with()


def test_service_cli_entrypoint_propagates_pywin32_exit_code():
    import student.service as service

    utility = MagicMock()
    utility.HandleCommandLine.return_value = 5
    with patch.object(service, "win32serviceutil", utility), patch.object(
        sys, "argv", [service.__file__, "start"]
    ):
        try:
            service.main()
            raised = False
        except SystemExit as exc:
            raised = True
            assert exc.code == 5

    assert raised


def test_service_pipe_loop_relaunches_agent_before_each_accept():
    import student.service as service_module

    if not hasattr(service_module, "LanClassroomStudentService"):
        pytest.skip("requires pywin32 service support")

    events = []
    stop_event = threading.Event()

    class Listener:
        def __init__(self, pipe_name):
            events.append(("listen", pipe_name))

        def accept(self):
            events.append(("accept", None))
            stop_event.set()
            raise OSError("test stop")

        def close(self):
            events.append(("close", None))

    instance = object.__new__(service_module.LanClassroomStudentService)
    instance._stop_event = stop_event
    instance._listener = None
    instance._client = MagicMock()

    with patch.object(
        service_module, "WindowsNamedPipeListener", Listener
    ), patch.object(
        service_module,
        "launch_installed_agent",
        side_effect=lambda: events.append(("launch", None)),
    ):
        instance._pipe_loop()

    assert events[:3] == [
        ("listen", service_module.STUDENT_PIPE_NAME),
        ("launch", None),
        ("accept", None),
    ]
    instance._client.attach_session_agent.assert_called_once_with(None)


def test_command_emits_received_before_running_handler():
    order = []
    executor = CommandExecutor()

    def handler(params):
        order.append("handler")
        return {"echo": params["value"]}

    executor.register_handler("echo", handler)
    execution = executor.execute(
        Command("command-1", "echo", {"value": 7}),
        lambda outcome: order.append(outcome.phase.value),
    )

    assert order == ["received", "handler"]
    assert execution.received.phase == OutcomePhase.RECEIVED
    assert execution.result.status == OutcomeStatus.SUCCEEDED
    assert execution.result.payload == {"echo": 7}


def test_duplicate_command_reuses_result_without_running_handler_again():
    calls = []
    executor = CommandExecutor()
    executor.register_handler("apply", lambda params: calls.append(params) or {"ok": True})
    command = Command("stable-id", "apply", {"setting": 1})

    first = executor.execute(command)
    duplicate = executor.execute(command)

    assert first.result.status == OutcomeStatus.SUCCEEDED
    assert duplicate.received.duplicate
    assert duplicate.result.duplicate
    assert duplicate.result.payload == {"ok": True}
    assert calls == [{"setting": 1}]


def test_handler_mutation_does_not_change_idempotency_fingerprint():
    calls = []
    executor = CommandExecutor()

    def handler(params):
        params["nested"]["value"] = 99
        calls.append(params)
        return {"ok": True}

    executor.register_handler("apply", handler)
    command = Command("immutable-id", "apply", {"nested": {"value": 1}})

    first = executor.execute(command)
    duplicate = executor.execute(
        Command("immutable-id", "apply", {"nested": {"value": 1}})
    )

    assert first.result.status == OutcomeStatus.SUCCEEDED
    assert duplicate.result.status == OutcomeStatus.SUCCEEDED
    assert duplicate.result.duplicate
    assert len(calls) == 1


def test_command_id_collision_is_rejected():
    executor = CommandExecutor()
    executor.register_handler("apply", lambda params: {"value": params["value"]})
    executor.execute(Command("same-id", "apply", {"value": 1}))

    collision = executor.execute(Command("same-id", "apply", {"value": 2}))

    assert collision.result.status == OutcomeStatus.FAILED
    assert "different content" in collision.result.error


def test_concurrent_duplicate_waits_for_original_result():
    entered = threading.Event()
    release = threading.Event()
    calls = []
    executor = CommandExecutor(duplicate_wait_timeout=2.0)

    def handler(params):
        calls.append(params)
        entered.set()
        release.wait(1.0)
        return {"done": True}

    executor.register_handler("slow", handler)
    command = Command("slow-id", "slow", {})
    first_result = []
    thread = threading.Thread(target=lambda: first_result.append(executor.execute(command)))
    thread.start()
    assert entered.wait(1.0)
    timer = threading.Timer(0.05, release.set)
    timer.start()

    duplicate = executor.execute(command)
    thread.join(1.0)
    timer.cancel()

    assert duplicate.result.status == OutcomeStatus.SUCCEEDED
    assert duplicate.result.duplicate
    assert len(calls) == 1
    assert first_result[0].result.payload == {"done": True}


def test_unsupported_and_failed_commands_have_result_outcomes():
    executor = CommandExecutor()
    unsupported = executor.execute(Command("x", "missing"))
    executor.register_handler("boom", lambda params: 1 / 0)
    failed = executor.execute(Command("y", "boom"))

    assert unsupported.result.status == OutcomeStatus.UNSUPPORTED
    assert failed.result.status == OutcomeStatus.FAILED
    assert "division by zero" in failed.result.error


def test_memory_channel_round_trip_and_close_signal():
    left, right = create_memory_channel_pair()
    message = {"type": "hello", "params": {"name": "student"}}

    left.send(message)
    message["params"]["name"] = "mutated"

    assert right.receive(0.1) == {
        "type": "hello",
        "params": {"name": "student"},
    }
    left.close()
    try:
        right.receive(0.1)
        raised = False
    except ChannelClosedError:
        raised = True
    assert raised


def test_named_pipe_adapters_use_guarded_af_pipe_factories():
    class FakeConnection:
        def close(self):
            pass

    class FakeListener:
        def __init__(self):
            self.closed = False

        def accept(self):
            return FakeConnection()

        def close(self):
            self.closed = True

    listener_calls = []
    connector_calls = []
    fake_listener = FakeListener()

    def listener_factory(address, **kwargs):
        listener_calls.append((address, kwargs))
        return fake_listener

    def connector(address, **kwargs):
        connector_calls.append((address, kwargs))
        return FakeConnection()

    listener = WindowsNamedPipeListener(
        "LanClassroom.Student.Session",
        listener_factory=listener_factory,
    )
    accepted = listener.accept()
    connected = WindowsNamedPipeConnector(connector).connect(
        "LanClassroom.Student.Session"
    )
    accepted.close()
    connected.close()
    listener.close()

    expected = r"\\.\pipe\LanClassroom.Student.Session"
    assert listener_calls == [(expected, {"family": "AF_PIPE", "authkey": None})]
    assert connector_calls == [(expected, {"family": "AF_PIPE", "authkey": None})]
    assert fake_listener.closed


def test_session_agent_proxy_dispatches_only_registered_actions():
    service_channel, agent_channel = create_memory_channel_pair()
    agent = SessionAgentRuntime(agent_channel, poll_interval=0.01)
    agent.register_handler("overlay.show", lambda params: {"text": params["text"]})
    agent.start()
    proxy = SessionAgentProxy(service_channel, default_timeout=1.0)

    success = proxy.call("overlay.show", {"text": "Quiet please"})
    unsupported = proxy.call("shell.execute", {})

    agent.stop()
    service_channel.close()
    assert success.success
    assert success.payload == {"text": "Quiet please"}
    assert not unsupported.success
    assert "unsupported" in unsupported.error


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows named pipes")
def test_real_windows_named_pipe_round_trip():
    pipe_name = "LanClassroomTest-{}".format(uuid.uuid4())
    listener = WindowsNamedPipeListener(pipe_name)
    received = []

    def serve():
        channel = listener.accept()
        received.append(channel.receive(2.0))
        channel.send({"kind": "response", "ready": True})
        channel.close()

    worker = threading.Thread(target=serve)
    worker.start()
    client = WindowsNamedPipeConnector().connect(pipe_name)
    client.send({"kind": "request", "action": "ping"})

    assert client.receive(2.0) == {"kind": "response", "ready": True}
    worker.join(3)
    client.close()
    listener.close()
    assert received == [{"kind": "request", "action": "ping"}]
    assert not worker.is_alive()


def test_student_service_runtime_delegates_session_command():
    service_channel, agent_channel = create_memory_channel_pair()
    agent = SessionAgentRuntime(agent_channel, poll_interval=0.01)
    agent.register_handler("overlay.show", lambda params: {"shown": params["text"]})
    agent.start()
    service = StudentServiceRuntime(session_agent=SessionAgentProxy(service_channel))
    service.register_session_action("silence", "overlay.show")

    execution = service.execute(Command("silence-1", "silence", {"text": "Class"}))

    agent.stop()
    service_channel.close()
    assert execution.result.status == OutcomeStatus.SUCCEEDED
    assert execution.result.payload == {"shown": "Class"}
