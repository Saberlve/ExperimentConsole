"""Browser leases for owned model processes; refreshes reconnect without motion."""
import threading
import time


class BrowserSession:
    CLOSE_GRACE = 15
    HEARTBEAT_TIMEOUT = 120

    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.clients = {}
        self.run_id = None
        self.draft = {}
        self.state = 'idle'
        self.error = ''
        self.stop = threading.Event()
        self.thread = None

    def status(self):
        with self.lock:
            return dict(enabled=self.app.control.enabled, run_id=self.run_id,
                        state=self.state, error=self.error, close_grace_seconds=self.CLOSE_GRACE)

    def _active_run_id(self):
        for kind in ('evaluation', 'reset'):
            if self.app.control.active(kind):
                run_id = self.app.control.jobs.get(kind, {}).get('run_id')
                if run_id:
                    return run_id
        # A finished client still owns its trial until the operator records
        # a result. Another tab must not replace that run or its note draft.
        job = self.app.control.jobs.get('evaluation', {})
        run_id = job.get('run_id')
        if run_id:
            with self.app.evaluation.lock:
                try:
                    record = self.app.evaluation.read('runs', run_id)
                except (OSError, ValueError):
                    # Missing/unreadable history must not prevent cleanup of
                    # the remaining owned processes.
                    return None
                if (record['cursor'] == job.get('trial_index')
                        and record['trials'][record['cursor']]['status'] == 'running'):
                    return run_id
        return None

    def _select_run(self, run_id):
        if run_id != self.run_id:
            self.run_id, self.draft = run_id, {}

    def heartbeat(self, payload, closing=False):
        if not self.app.control.enabled:
            return self.status()
        client = payload.get('client_id')
        if not isinstance(client, str) or not 1 <= len(client) <= 100:
            raise ValueError('缺少浏览器会话标识')
        with self.lock:
            now = time.monotonic()
            if closing and client not in self.clients:
                return self.status()
            self.clients[client] = now + (self.CLOSE_GRACE if closing else self.HEARTBEAT_TIMEOUT)
            if not closing:
                self.state, self.error = 'connected', ''
            owned_run_id = self._active_run_id()
            if not owned_run_id and not self.app.control.active('server'):
                self.run_id, self.draft = None, {}
            if owned_run_id:
                self._select_run(owned_run_id)
            run_id = payload.get('run_id')
            if run_id and (not owned_run_id or run_id == owned_run_id):
                record = self.app.evaluation.read('runs', run_id)
                server = self.app.control.jobs.get('server', {})
                if (owned_run_id or (self.app.control.active('server')
                        and server.get('model', '').lower() == record['model'].lower()
                        and server.get('checkpoint') == record['checkpoint'])):
                    self._select_run(run_id)
                    if payload.get('cursor') == record['cursor']:
                        self.draft = {k: payload[k] for k in ('cursor', 'note') if k in payload}
            if closing:
                self.state = 'waiting_for_reconnect'
            if self.thread is None:
                self.thread = threading.Thread(target=self._watch, daemon=True)
                self.thread.start()
            return self.status()

    def _watch(self):
        while not self.stop.wait(1):
            self.expire()

    def expire(self, now=None):
        with self.lock:
            now = time.monotonic() if now is None else now
            if not self.clients or self.state == 'error' or any(deadline > now for deadline in self.clients.values()):
                return False
            self.state = 'saving_and_stopping'
            control = self.app.control
            owned_run_id = self._active_run_id()
            if owned_run_id:
                self._select_run(owned_run_id)
            errors = []

            def attempt(action):
                try:
                    action()
                    return True
                except Exception as exc:
                    errors.append(str(exc))
                    return False

            # A failed snapshot/export must never bypass process cleanup.
            if self.run_id:
                attempt(lambda: self.app.evaluation.save_results(self.run_id))
            stopped = True
            if control.active('evaluation') or control.active('reset'):
                stopped = attempt(control.emergency_stop)
            if self.run_id and stopped:
                def save_aborted():
                    with self.app.evaluation.lock:
                        record = self.app.evaluation.read('runs', self.run_id)
                        trial = record['trials'][record['cursor']]
                        if trial['status'] == 'running':
                            note = self.draft.get('note', trial.get('note', '')) if self.draft.get('cursor') == record['cursor'] else trial.get('note', '')
                            self.app.evaluation.transition(dict(run_id=record['id'], revision=record['revision'],
                                action='result', result='aborted', note=note))
                        self.app.evaluation.save_results(self.run_id)
                attempt(save_aborted)
            attempt(control.stop_server)
            attempt(self.app.release_preview)
            if errors:
                self.state, self.error = 'error', '; '.join(errors)
                return False
            self.clients.clear()
            self.run_id, self.draft = None, {}
            self.state = 'closed'
            return True

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
