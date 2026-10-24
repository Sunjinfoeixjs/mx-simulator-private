# -*- coding: utf-8 -*-
"""MX-13 encrypted physical-TCP S2C proof. Actual ciphertext is decrypted for routing."""
import hashlib,json,pathlib,shutil,socket,struct,sys,time
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from run_emission_e2e import m,EmissionObserver,install_on_handle,exact,recv_s2c,hello,login,require
import mx_crypto,lzss

class EncryptedObserver(EmissionObserver):
    def __init__(self,*args,seed):
        super().__init__(*args)
        self.rx_cipher=mx_crypto.RealCipher(seed)
        self.hello_sent=False
        self.plain_buf=bytearray()
        self.total_cipher_wire_bytes=0
        self.confirmed_lzss=0
    def sent(self,socket_key,data):
        if not self.hello_sent:
            # Hello is strictly plaintext: before the real client turns encryption on.
            super().sent(socket_key,data)
            self.hello_sent=True
            return
        self.total_cipher_wire_bytes+=len(data)
        self.plain_buf.extend(self.rx_cipher.crypt_rx(data))
        while self.plain_buf:
            start=1;n=self.plain_buf[0]
            if n==255:
                if len(self.plain_buf)<3:break
                start=3;n=struct.unpack("<H",self.plain_buf[1:3])[0]
            if n<1:raise AssertionError("Encrypted physical length is invalid")
            if len(self.plain_buf)<start+n:break
            recovered_wire=bytes(self.plain_buf[:start+n]);del self.plain_buf[:start+n]
            compressed=recovered_wire[start:]
            decompressed=lzss.decompress(compressed)
            if not decompressed or lzss.compress(decompressed)!=compressed:
                raise AssertionError("LZSS decompression or recompression mismatch")
            op=decompressed[0]
            route=self.by_opcode.get(op)
            self.confirmed_lzss+=1
            self._append(dict(test=self.test_name,boundary="socket.sendall",
                status="SENT",physical_mode="xor_encrypted_lzss",
                route_id=route["id"] if route else None,
                handler=route["handler"] if route else None,
                opcode=op,frame_bytes=len(recovered_wire),
                recovered_wire_sha256=hashlib.sha256(recovered_wire).hexdigest(),
                uncompressed_sha256=hashlib.sha256(decompressed).hexdigest(),
                logical_payload_sha256=hashlib.sha256(decompressed).hexdigest(),
                call_path=self._callpath()))
    def finalize(self):
        pending=len(self.plain_buf)
        if pending:self._append(dict(status="INCOMPLETE_FRAME",pending_bytes=pending))
        return pending

def recv_encrypted(sock,rx):
    sz=rx.crypt_rx(exact(sock,1))[0]
    if sz==255:sz=struct.unpack("<H",rx.crypt_rx(exact(sock,2)))[0]
    require(0<sz<100000,"Invalid decrypted length "+str(sz))
    compressed=rx.crypt_rx(exact(sock,sz))
    decoded=lzss.decompress(compressed)
    require(bool(decoded) and lzss.compress(decoded)==compressed,
            "Client-side LZSS roundtrip mismatch")
    return decoded[0],decoded[1:],sz

def send_encrypted(sock,tx,opcode,body=b""):
    # C2S differs from S2C: [opcode][length][body], NOT [length][opcode][body].
    if len(body)>=255:packet=bytes([opcode])+b"\xff"+struct.pack("<H",len(body))+body
    else:packet=bytes([opcode,len(body)])+body
    sock.sendall(tx.crypt_tx(packet))

def until_encrypted(sock,rx,wanted,limit=250):
    seen=[]
    for _ in range(limit):
        op,body,n=recv_encrypted(sock,rx)
        seen.append(op)
        if op in wanted:return seen,body
    raise AssertionError("Encrypted response not found; last: "+repr(seen[-20:]))

def run():
    runtime=HERE/"crypto_runtime"
    if runtime.exists():shutil.rmtree(runtime)
    (runtime/"save").mkdir(parents=True)
    (runtime/"logs").mkdir()
    seed=m.SEED_DEFAULT
    observer=EncryptedObserver(HERE/"route_inventory.json",
       HERE/"emission_encrypted_actual.jsonl","mx13_P0_true_encrypted_TPC_LZSS",
       seed=seed)
    old=m.FRAME_BATCH;m.FRAME_BATCH=True
    undo=install_on_handle(m,observer)
    server=m.M4Server("127.0.0.1",0,seed,encounter_every=200,enemies=1,turn_timeout=7,
                      savedir=str(runtime/"save"),logdir=str(runtime/"logs"),encrypt=True)
    server.log=lambda line:None;server._save_async=False
    port=server.serve_background(port=0)
    c=None;traces={}
    try:
        c=socket.create_connection(("127.0.0.1",port),timeout=5)
        hop,_=recv_s2c(c);require(hop==1,"Plaintext hello must be 1")
        c.sendall(hello(seed))
        client=mx_crypto.RealCipher(seed)
        send_encrypted(c,client,1,login("mx13crypt"))
        seen,body=until_encrypted(c,client,{2})
        traces["login"]=seen
        if not body[0]:
            send_encrypted(c,client,3,"加密测试".encode("gbk")[:16].ljust(20,b"\0")+b"\0"*4)
            traces["create"],_=until_encrypted(c,client,{3})
        send_encrypted(c,client,2,struct.pack("<I",1))
        traces["world"],_=until_encrypted(c,client,{97})
        require(16 in traces["world"] and 96 in traces["world"],"Encrypted world frames absent")
        send_encrypted(c,client,m.C2S_NPC_GETDETAILS,struct.pack("<I",0))
        traces["craft_details"],_=until_encrypted(c,client,{151})
        send_encrypted(c,client,m.C2S_NPC_GETCOST,bytes([0])+struct.pack("<I",0))
        traces["craft_cost"],_=until_encrypted(c,client,{155})
        send_encrypted(c,client,m.C2S_PRODUCE,bytes([m.PRODUCE_SUB_MINERALS]))
        traces["minerals"],_=until_encrypted(c,client,{87})
    finally:
        if c:
            try:c.close()
            except OSError:pass
        server.stop();time.sleep(0.2)
        undo();m.FRAME_BATCH=old
    require(observer.finalize()==0,"Encrypted fragmented stream left undecoded")
    require(not observer.observation_errors,"Observer errors")
    sent=[e for e in observer.events if e.get("status")=="SENT"]
    encrypted=[e for e in sent if e.get("physical_mode")=="xor_encrypted_lzss"]
    require(encrypted and all(e["route_id"] for e in encrypted),"Unmapped encrypted route")
    require(observer.total_cipher_wire_bytes>0 and observer.confirmed_lzss>=4,"No encrypted S2C")
    actual={e["opcode"] for e in encrypted}
    require({2,16,96,97,151,155,87}.issubset(actual),"Missing encrypted expected opcodes: "+str(actual))
    result={"test":"mx13_P0_true_encrypted_TCP_LZSS","status":"PASS",
      "seed":hex(seed),"hello_plain":True,
      "successful_encrypted_frames":len(encrypted),
      "successful_encrypted_unique_routes":len({e["route_id"] for e in encrypted}),
      "encrypted_physical_wire_bytes":observer.total_cipher_wire_bytes,
      "independently_lzss_verified_frames":observer.confirmed_lzss,
      "traces":traces,
      "no_original_GUI_client":True}
    (HERE/"mx13_crypto_results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":run()
