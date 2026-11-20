import asyncio
import threading
from queue import SimpleQueue


class AgentRunner:
    def __init__(self):
        self.events = SimpleQueue()
        self.thread = None
        self.stopping = threading.Event()

    def start(self, args):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()

        def worker():
            try:
                from .server import run
                asyncio.run(run(args, self.stopping, lambda *event: self.events.put(event)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("off", ""))

        self.thread = threading.Thread(target=worker, name="Mark Mouse e Teclado Control agent")
        self.thread.start()

    def stop(self):
        self.stopping.set()
