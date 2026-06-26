import pytest
import time
import threading
from common.tcp_conn import TCPServer, TCPClient, TCPConnection
from common.protocol import MessageType, build_message, serialize_message, deserialize_message


@pytest.fixture
def tcp_server():
    server = TCPServer(port=0)
    server.start()
    time.sleep(0.1)
    actual_port = server._sock.getsockname()[1]
    yield server, actual_port
    server.stop()
    time.sleep(0.1)


class TestTCPServerClient:
    def test_connect(self, tcp_server):
        server, port = tcp_server
        client = TCPClient()
        connected = client.connect("127.0.0.1", port)
        assert connected is True
        assert client.is_connected() is True
        client.disconnect()

    def test_send_receive_message(self, tcp_server):
        server, port = tcp_server
        received = []
        event = threading.Event()

        def on_message(conn, msg):
            received.append(msg)
            event.set()

        server.on_message = on_message
        client = TCPClient()
        client.connect("127.0.0.1", port)
        time.sleep(0.1)

        msg = build_message(MessageType.STUDENT_HEARTBEAT, {"data": "test"})
        client.send_message(msg)

        event.wait(timeout=2)
        assert len(received) > 0
        assert received[0]["type"] == MessageType.STUDENT_HEARTBEAT.value
        assert received[0]["params"]["data"] == "test"
        client.disconnect()

    def test_server_to_client(self, tcp_server):
        server, port = tcp_server
        received = []
        event = threading.Event()

        client = TCPClient()
        client.on_message = lambda msg: (received.append(msg), event.set())
        client.connect("127.0.0.1", port)
        time.sleep(0.1)

        conns = server.get_all_connections()
        assert len(conns) == 1
        msg = build_message(MessageType.BLACK_SCREEN, {"enable": True})
        conns[0].send_message(msg)

        event.wait(timeout=2)
        assert len(received) > 0
        assert received[0]["type"] == MessageType.BLACK_SCREEN.value
        assert received[0]["params"]["enable"] is True
        client.disconnect()

    def test_multiple_clients(self, tcp_server):
        server, port = tcp_server
        clients = []
        for i in range(3):
            client = TCPClient()
            client.connect("127.0.0.1", port)
            clients.append(client)
        time.sleep(0.2)
        assert len(server.get_all_connections()) == 3
        for c in clients:
            c.disconnect()
        time.sleep(0.2)
        assert len(server.get_all_connections()) == 0

    def test_sticky_packet(self, tcp_server):
        server, port = tcp_server
        received = []
        event = threading.Event()

        def on_message(conn, msg):
            received.append(msg)
            if len(received) >= 3:
                event.set()

        server.on_message = on_message

        import socket
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.connect(("127.0.0.1", port))
        time.sleep(0.1)

        m1 = build_message(MessageType.STUDENT_HEARTBEAT, {"n": 1})
        m2 = build_message(MessageType.STUDENT_HEARTBEAT, {"n": 2})
        m3 = build_message(MessageType.STUDENT_HEARTBEAT, {"n": 3})
        combined = serialize_message(m1) + serialize_message(m2) + serialize_message(m3)
        client_sock.sendall(combined)

        event.wait(timeout=2)
        assert len(received) >= 3
        assert received[0]["params"]["n"] == 1
        assert received[1]["params"]["n"] == 2
        assert received[2]["params"]["n"] == 3
        client_sock.close()

    def test_disconnect_callback(self, tcp_server):
        server, port = tcp_server
        disconnected = []
        event = threading.Event()

        def on_disconnect(conn):
            disconnected.append(conn.addr)
            event.set()

        server.on_disconnect = on_disconnect
        client = TCPClient()
        client.connect("127.0.0.1", port)
        time.sleep(0.1)
        client.disconnect()

        event.wait(timeout=2)
        assert len(disconnected) > 0

    def test_broadcast_message(self, tcp_server):
        server, port = tcp_server
        client1_received = []
        client2_received = []
        event1 = threading.Event()
        event2 = threading.Event()

        client1 = TCPClient()
        client1.on_message = lambda msg: (client1_received.append(msg), event1.set())
        client1.connect("127.0.0.1", port)

        client2 = TCPClient()
        client2.on_message = lambda msg: (client2_received.append(msg), event2.set())
        client2.connect("127.0.0.1", port)
        time.sleep(0.2)

        msg = build_message(MessageType.BLACK_SCREEN, {"enable": True})
        server.broadcast_message(msg)

        event1.wait(timeout=2)
        event2.wait(timeout=2)
        assert len(client1_received) > 0
        assert len(client2_received) > 0
        assert client1_received[0]["type"] == MessageType.BLACK_SCREEN.value
        assert client2_received[0]["type"] == MessageType.BLACK_SCREEN.value
        client1.disconnect()
        client2.disconnect()
