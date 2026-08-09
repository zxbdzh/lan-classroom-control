class FakeNoSuchProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakeTimeoutExpired(Exception):
    pass


class FakeProcess:
    def __init__(
        self,
        pid,
        name,
        executable,
        creation_time,
        username="student",
        wait_exception=None,
    ):
        self.pid = pid
        self._name = name
        self._executable = executable
        self._creation_time = creation_time
        self._wait_exception = wait_exception
        self.terminate_calls = 0
        self.kill_calls = 0
        self.info = {
            "pid": pid,
            "name": name,
            "exe": executable,
            "create_time": creation_time,
            "username": username,
        }

    def name(self):
        return self._name

    def exe(self):
        return self._executable

    def create_time(self):
        return self._creation_time

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self._wait_exception = None

    def wait(self, timeout):
        if self._wait_exception is not None:
            raise self._wait_exception
        return 0


class FakeProcessProvider:
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeAccessDenied
    TimeoutExpired = FakeTimeoutExpired

    def __init__(self, processes):
        self.processes = {process.pid: process for process in processes}

    def process_iter(self, attributes):
        return list(self.processes.values())

    def Process(self, pid):
        if pid not in self.processes:
            raise FakeNoSuchProcess(pid)
        return self.processes[pid]


def _controller(processes, **kwargs):
    from student.core.v2.processes import ProcessController

    return ProcessController(
        process_provider=FakeProcessProvider(processes),
        own_pid=999,
        protected_names=[],
        **kwargs,
    )


def test_list_processes_exposes_instance_identity_and_protection():
    from student.core.v2.processes import ProcessController

    system = FakeProcess(4, "System", None, 1.0, "SYSTEM")
    own = FakeProcess(50, "LanClassroom-Student.exe", r"C:\Student.exe", 2.0)
    regular = FakeProcess(60, "editor.exe", r"C:\Editor.exe", 3.0)
    controller = ProcessController(
        process_provider=FakeProcessProvider([system, own, regular]),
        own_pid=50,
    )

    listed = controller.list_processes()

    assert [(item.pid, item.creation_time) for item in listed] == [
        (4, 1.0),
        (50, 2.0),
        (60, 3.0),
    ]
    assert listed[0].protected
    assert listed[1].protected
    assert not listed[2].protected


def test_terminate_requires_matching_creation_time():
    from student.core.v2.processes import ProcessTerminationStatus

    process = FakeProcess(100, "game.exe", r"C:\Game.exe", 20.0)
    controller = _controller([process])

    result = controller.terminate_process(100, 19.0)

    assert result.status == ProcessTerminationStatus.STALE_PROCESS
    assert process.terminate_calls == 0


def test_terminate_regular_process():
    from student.core.v2.processes import ProcessTerminationStatus

    process = FakeProcess(101, "game.exe", r"C:\Game.exe", 20.0)
    controller = _controller([process])

    result = controller.terminate_process(101, 20.0)

    assert result.status == ProcessTerminationStatus.TERMINATED
    assert result.success
    assert process.terminate_calls == 1
    assert process.kill_calls == 0


def test_terminate_protects_own_process_and_configured_names():
    from student.core.v2.processes import ProcessController, ProcessTerminationStatus

    own = FakeProcess(200, "python.exe", r"C:\Python\python.exe", 1.0)
    system = FakeProcess(201, "critical.exe", r"C:\critical.exe", 2.0)
    controller = ProcessController(
        process_provider=FakeProcessProvider([own, system]),
        own_pid=200,
        protected_names=["critical.exe"],
    )

    own_result = controller.terminate_process(200, 1.0)
    system_result = controller.terminate_process(201, 2.0)

    assert own_result.status == ProcessTerminationStatus.PROTECTED
    assert system_result.status == ProcessTerminationStatus.PROTECTED
    assert own.terminate_calls == 0
    assert system.terminate_calls == 0


def test_terminate_timeout_can_optionally_force_kill():
    from student.core.v2.processes import ProcessTerminationStatus

    timeout = FakeTimeoutExpired("slow")
    process = FakeProcess(300, "game.exe", r"C:\Game.exe", 4.0, wait_exception=timeout)
    controller = _controller([process])

    result = controller.terminate_process(300, 4.0, force=True)

    assert result.status == ProcessTerminationStatus.TERMINATED
    assert result.forced
    assert process.terminate_calls == 1
    assert process.kill_calls == 1


def test_missing_process_returns_structured_result():
    from student.core.v2.processes import ProcessTerminationStatus

    controller = _controller([])

    result = controller.terminate_process(404, 1.0)

    assert result.status == ProcessTerminationStatus.NOT_FOUND
    assert not result.success
