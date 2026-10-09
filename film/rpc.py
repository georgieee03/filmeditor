"""Bounded JSONL RPC transport for trusted local child processes."""
from __future__ import annotations
from collections import deque
import json
import os
import queue
import signal
import subprocess
import threading

class RpcError(RuntimeError):
    pass

class JsonlProcess:
    def __init__(self, command, *, cwd=None, env=None, on_message=None, jsonrpc=True):
        self.lock = threading.RLock()
        self.pending = {}
        self.next_id = 0
        self.stderr = deque(maxlen=80)
        self.on_message = on_message or (lambda m: None)
        self.jsonrpc = jsonrpc
        self.closed = False
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()

    def _errors(self):
        try:
            while True:
                line = self.process.stderr.readline(8192)
                if not line: break
                self.stderr.append(line.decode('utf-8', 'replace')[-2000:])
        except (OSError, ValueError):
            pass

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(16 * 1024**2 + 1)
                if not line: break
                if len(line) > 16 * 1024**2: raise RpcError('Engine exceeded message limit')
                try: message = json.loads(line)
                except (ValueError, UnicodeError):
                    self.stderr.append('Non-JSON stdout: ' + line[:500].decode(errors='replace'))
                    continue
                if not isinstance(message, dict): continue
                if 'id' in message and 'method' not in message:
                    with self.lock: target = self.pending.get(message['id'])
                    if target: target.put(message)
                else:
                    try: self.on_message(message)
                    except Exception as exc: self.stderr.append('Event handler: ' + str(exc))
        except (OSError, ValueError, RpcError) as exc:
            self.stderr.append(str(exc))
        finally:
            with self.lock:
                self.closed = True
                for q in self.pending.values():
                    q.put({'error': {'message': 'Engine disconnected. ' + ''.join(self.stderr)[-1500:]}})

    def send(self, message):
        if self.jsonrpc: message = {'jsonrpc': '2.0', **message}
        raw = (json.dumps(message, ensure_ascii=False) + '\n').encode()
        with self.lock:
            if self.closed or self.process.poll() is not None: raise RpcError('Engine is not running')
            self.process.stdin.write(raw)
            self.process.stdin.flush()

    def request(self, method, params=None, timeout=60):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            q = self.pending[ident] = queue.Queue()
        try:
            self.send({'id': ident, 'method': method, 'params': params or {}})
            try: response = q.get(timeout=timeout)
            except queue.Empty as exc:
                raise RpcError(f'{method} timed out; inspect state before retrying a write') from exc
            if 'error' in response: raise RpcError(str(response['error']))
            return response.get('result', {})
        finally:
            with self.lock: self.pending.pop(ident, None)

    def notify(self, method, params=None):
        self.send({'method': method, 'params': params or {}})

    def close(self):
        def signal_owned(sig):
            try: os.killpg(self.process.pid,sig)
            except PermissionError as exc:
                # macOS can report EPERM for a group already in its exit path.
                # Only accept the race after this exact child is reaped.
                try: self.process.wait(timeout=.25)
                except subprocess.TimeoutExpired: raise exc
        if self.process.poll() is None:
            try:
                signal_owned(signal.SIGTERM)
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                signal_owned(signal.SIGKILL)
                self.process.wait(timeout=4)
            except ProcessLookupError: pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try: stream.close()
            except OSError: pass
