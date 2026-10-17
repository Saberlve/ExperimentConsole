"""Explicit fake hardware/process objects, shared by unit and browser tests."""
import csv
import io
import threading
from adapters.replay import LegacyReplayAdapter


def action_csv(changes=None):
    from backend.replay import COLUMNS
    output = io.StringIO()
    writer = csv.writer(output)
    header = ['original_row', 'timestamp_s'] + COLUMNS
    writer.writerow(header)
    for index in range(3):
        row = dict(zip(header, [index, index/15, .160 + index*.001, .001, .135, 1, 0, 0, 0, -1, 0, .1]))
        row.update((changes or {}).get(index, {}))
        writer.writerow([row[key] for key in header])
    return output.getvalue().encode()


class FakeProcess:
    def __init__(self):
        self.done = threading.Event()
        self.returncode = 0
        self.stdout = self.lines()
        self.signals = []

    def lines(self):
        yield '[run] MOCK hardware process; no robot connected\n'
        self.done.wait(10)
        yield '[done] MOCK command stream finished\n'

    def poll(self):
        return self.returncode if self.done.is_set() else None

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError('fake process still active')
        return self.returncode

    def send_signal(self, signal):
        self.signals.append(signal)
        self.returncode = 130
        self.done.set()


class FakeReplayAdapter(LegacyReplayAdapter):
    def __init__(self, root, config):
        super().__init__(root, config)
        self.launches = []
        self.stops = []

    def launch(self, path, fps, ip):
        process = FakeProcess()
        self.launches.append(process)
        return process

    def stop(self, ip):
        self.stops.append(ip)
        return 'MOCK controller stop acknowledged; no hardware'
