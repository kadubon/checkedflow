"""TCP relays that cut real validator links without changing host firewall rules."""

import select
import socket
import threading


class Relay:
    def __init__(self, listen, target):
        self.target = target
        self.blocked = False
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.pairs = []
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", listen))
        self.server.listen(16)
        self.server.settimeout(0.2)
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.closed.is_set():
            try:
                incoming, _ = self.server.accept()
            except (TimeoutError, OSError):
                continue
            with self.lock:
                if self.blocked:
                    incoming.close()
                    continue
                try:
                    outgoing = socket.create_connection(("127.0.0.1", self.target), timeout=1)
                except OSError:
                    incoming.close()
                    continue
                pair = (incoming, outgoing)
                self.pairs.append(pair)
            threading.Thread(target=self.forward, args=(pair,), daemon=True).start()

    def forward(self, pair):
        try:
            while not self.closed.is_set():
                readable, _, _ = select.select(pair, [], [], 0.2)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = pair[1] if source is pair[0] else pair[0]
                    destination.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                for connection in pair:
                    connection.close()
                if pair in self.pairs:
                    self.pairs.remove(pair)

    def cut(self, blocked):
        with self.lock:
            self.blocked = blocked
            if blocked:
                for pair in self.pairs:
                    for connection in pair:
                        connection.close()

    def close(self):
        self.closed.set()
        self.cut(True)
        self.server.close()
        self.thread.join(timeout=2)


class FaultNetwork:
    def __init__(self, cluster):
        self.relays = {}
        for source in range(4):
            path = cluster.directory / f"node{source}/config/config.toml"
            config = path.read_text().replace("pex = true", "pex = false")
            for target in range(4):
                if source == target:
                    continue
                port = 31600 + 10 * source + target
                self.relays[source, target] = Relay(port, cluster.port(target, 1))
                config = config.replace(
                    f"@127.0.0.1:{cluster.port(target, 1)}", f"@127.0.0.1:{port}"
                )
            path.write_text(config)

    def isolate(self, node, blocked):
        for (source, target), relay in self.relays.items():
            if node in (source, target):
                relay.cut(blocked)

    def close(self):
        for relay in self.relays.values():
            relay.close()
