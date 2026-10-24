# -*- coding: utf-8 -*-
"""Test-only observer for actual sendall success; no edits to game server source.

Scope: unencrypted P0 baseline socket frames. The observer records only after the
underlying sendall returns successfully. A buffered frame is not an emission.
"""
import hashlib, inspect, json, pathlib, struct, threading, socket

class EmissionObserver:
    def __init__(self, inventory, sink, test_name):
        raw=json.loads(pathlib.Path(inventory).read_text(encoding="utf-8"))
        self.inventory=raw
        self.by_opcode={r["opcode"]:r for r in raw["routes"] if r["opcode"] is not None}
        self.sink=pathlib.Path(sink)
        self.test_name=test_name
        self.events=[]
        self.failures=[]
        self.observation_errors=[]  # Record observer failures without altering socket outcomes.
        self._lock=threading.Lock()
        self._streams={}
        self._ordinal=0
        self.sink.parent.mkdir(parents=True,exist_ok=True)
        self.sink.write_text("",encoding="utf-8")
    def _callpath(self):
        frames=inspect.stack(context=0)
        return ["%s:%s:%s"%(pathlib.Path(x.filename).name,x.function,x.lineno) for x in frames[3:10]]
    def _append(self,ev):
        with self._lock:
            self._ordinal+=1
            ev["seq"]=self._ordinal
            self.events.append(ev)
            with self.sink.open("a",encoding="utf-8") as f:
                f.write(json.dumps(ev,ensure_ascii=False,sort_keys=True)+"\n")
    def failed_send(self,socket_key,exc,data):
        ev={"test":self.test_name,"boundary":"socket.sendall","status":"SEND_ERROR",
            "exception_type":type(exc).__name__,"attempted_bytes":len(data),
            "payload_sha256":hashlib.sha256(data).hexdigest(),
            "call_path":self._callpath()}
        self.failures.append(ev)
        self._append(ev)
    def sent(self,socket_key,data):
        if not data:
            return
        # A stream parser is essential when a successful syscall contains N coalesced frames.
        buf=self._streams.setdefault(socket_key,bytearray())
        buf.extend(data)
        while buf:
            first=buf[0]
            if first==255:
                if len(buf)<3:break
                n=struct.unpack("<H",buf[1:3])[0];start=3
            else:
                n=first;start=1
            if n<1:
                self._append(dict(test=self.test_name,boundary="socket.sendall",
                                  status="DECODE_ERROR",reason="empty S2C payload"))
                buf.clear();break
            if len(buf)<start+n:break
            frame=bytes(buf[:start+n]);del buf[:start+n]
            opcode=frame[start]
            route=self.by_opcode.get(opcode)
            self._append(dict(test=self.test_name,boundary="socket.sendall",
                status="SENT",route_id=route["id"] if route else None,
                handler=route["handler"] if route else None,opcode=opcode,
                excluded_from_160=route is None,
                tag=route["tag"] if route else None,
                frame_bytes=len(frame),
                frame_sha256=hashlib.sha256(frame).hexdigest(),
                call_path=self._callpath()))
    def finalize(self):
        pending=sum(len(x) for x in self._streams.values())
        if pending:
            self._append(dict(test=self.test_name,boundary="socket.sendall",
                              status="INCOMPLETE_FRAME",pending_bytes=pending))
        return pending

class BoundarySocket:
    def __init__(self, conn, observer):
        self._real_conn=conn
        self._observer=observer
    def sendall(self,data):
        b=bytes(data)
        try:
            result=self._real_conn.sendall(data)
        except Exception as exc:
            try:
                self._observer.failed_send(id(self),exc,b)
            except Exception as observation_exc:
                self._observer.observation_errors.append(repr(observation_exc))
            raise  # Preserve the original socket exception, never mask it.
        # Strictly *after* physical sendall; observer I/O must not change the result.
        try:
            self._observer.sent(id(self),b)
        except Exception as observation_exc:
            self._observer.observation_errors.append(repr(observation_exc))
        return result
    def __getattr__(self,name):
        return getattr(self._real_conn,name)

def install_on_handle(m,observer):
    """Patch one test process only, at the *real socket* underneath FrameBatcher.

    Maintains the same batching, ordering, underlying socket, exception and locks.
    Uninstall after closing test server; never enable in production.
    """
    original=m.M4Server.handle
    def wrapped(self,conn):
        if isinstance(conn,m.FrameBatcher) and isinstance(conn._conn,socket.socket):
            conn._conn=BoundarySocket(conn._conn,observer)
            # Keep the observer for run()'s final post-handle batch flush.
            return original(self,conn)
        if isinstance(conn,socket.socket):
            return original(self,BoundarySocket(conn,observer))
        # FakeConn / in-memory transport is never considered a real TCP emission.
        return original(self,conn)
    m.M4Server.handle=wrapped
    return lambda: setattr(m.M4Server,"handle",original)
