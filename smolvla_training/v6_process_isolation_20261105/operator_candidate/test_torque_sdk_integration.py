"""SDK 실제 함수4개 + 실제 WriteGuard + 가짜 포트. 실제 장치 접근0."""
import contextlib
import json
from pathlib import Path
import unittest
import arm_source as arm
from individual_reply_guard import StrictIndividualReplies,ReplyRejected


def sdk_handler():
    record=json.loads((Path(__file__).parent/"torque_tx_sdk_fixture.json").read_text(encoding="utf-8"))
    scope=dict(COMM_SUCCESS=0,COMM_PORT_BUSY=-1,COMM_TX_ERROR=-2,COMM_TX_FAIL=-3,COMM_NOT_AVAILABLE=-4,
               BROADCAST_ID=254,INST_READ=2,INST_SYNC_WRITE=131,PKT_HEADER0=0,PKT_HEADER1=1,
               PKT_ID=2,PKT_LENGTH=3,PKT_INSTRUCTION=4,PKT_ERROR=4,PKT_PARAMETER0=5,TXPACKET_MAX_LEN=250)
    for source in record["methods"].values():exec(compile(source,"저장된 설치SDK", "exec"),scope)
    methods={name:scope[name] for name in record["methods"]}
    def rx(self,port):
        port.is_using=False
        body=bytes((255,255,1,3,0,1))
        return body+bytes(((~sum(body[2:]))&255,)),0
    methods["rxPacket"]=rx
    return type("InstalledSDKFixture",(),methods)()


class FakePort:
    def __init__(self):self.is_using=False;self.sent=[];self.short=False;self.opened=False
    def openPort(self):self.opened=True;return True
    def closePort(self):self.opened=False
    def clearPort(self):pass
    def setPacketTimeout(self,value):pass
    def writePort(self,packet):
        self.sent.append(bytes(packet))
        return len(packet)-1 if self.short else len(packet)


def send(handler,port,*,address=40,width=1,ids=None,value=1):
    ids=list(range(1,7)) if ids is None else ids
    params=[]
    for motor in ids:params.extend([motor,*int(value).to_bytes(width,"little")])
    return handler.syncWriteTxOnly(port,address,width,params,len(params))


@contextlib.contextmanager
def fixture(mode="enable",*,link=True):
    events=[];port=FakePort();guard=arm.WriteGuard(mode,events.append);handler=sdk_handler()
    with guard.installed(FakePort):
        guard.phase="open";port.openPort()
        guard.confirm_existing_goal(dict.fromkeys(arm.JOINTS,1000),dict.fromkeys(arm.JOINTS,1000))
        with StrictIndividualReplies(handler,write_guard=guard if link else None) as strict:
            try:yield port,guard,handler,strict,events
            finally:port.closePort()


class Tests(unittest.TestCase):
    def test_real_sdk_enable_chain_transmits_exactly_one_torque_packet(self):
        with fixture() as (port,guard,handler,strict,events):
            with guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                self.assertEqual(send(handler,port),0)
            self.assertEqual(len(port.sent),1)
            self.assertEqual(port.sent[0][4:7],bytes((131,40,1)))
            self.assertEqual(list(port.sent[0][7:-1:2]),list(range(1,7)))
            self.assertEqual(list(port.sent[0][8:-1:2]),[1]*6)
            self.assertEqual(guard.writes,1)
            self.assertEqual(sum(e["event"]=="WRITE_TRANSMITTED" for e in events),1)

    def test_real_sdk_disable_chain_requires_explicit_zero_permission(self):
        with fixture("disable") as (port,guard,handler,strict,events):
            with guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,0)):
                self.assertEqual(send(handler,port,value=0),0)
            self.assertEqual(len(port.sent),1)
            self.assertEqual(list(port.sent[0][8:-1:2]),[0]*6)

    def test_without_write_link_reproduces_previously_blocked_path(self):
        with fixture(link=False) as (port,guard,handler,strict,events):
            with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                send(handler,port)
            self.assertEqual(port.sent,[])
            self.assertEqual(guard.writes,0)

    def test_no_explicit_permission_never_reaches_fake_port(self):
        with fixture() as (port,guard,handler,strict,events):
            with self.assertRaises(ReplyRejected):send(handler,port)
            self.assertEqual(port.sent,[])

    def test_goal_write_still_denied_before_transport(self):
        with fixture() as (port,guard,handler,strict,events):
            with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                send(handler,port,address=42,width=2,value=1000)
            self.assertEqual(port.sent,[])

    def test_mode_write_still_denied_before_transport(self):
        with fixture() as (port,guard,handler,strict,events):
            with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                send(handler,port,address=33)
            self.assertEqual(port.sent,[])

    def test_wrong_ids_or_torque_values_never_transmit(self):
        for changes in (dict(ids=[1,2,3,4,5,5]),dict(value=0)):
            with self.subTest(changes=changes),fixture() as (port,guard,handler,strict,events):
                with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                    send(handler,port,**changes)
                self.assertEqual(port.sent,[])

    def test_second_attempt_refused_after_first_fake_packet(self):
        with fixture() as (port,guard,handler,strict,events):
            with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                send(handler,port);send(handler,port)
            self.assertEqual(len(port.sent),1)
            self.assertEqual(guard.writes,1)

    def test_partial_transmission_never_retries(self):
        with fixture() as (port,guard,handler,strict,events):
            port.short=True
            with self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
                send(handler,port)
            with self.assertRaises(ReplyRejected):send(handler,port)
            self.assertEqual(len(port.sent),1)
            self.assertFalse(any(e["event"]=="WRITE_TRANSMITTED" for e in events))

    def test_read_validation_remains_enabled_after_authorized_write(self):
        with fixture() as (port,guard,handler,strict,events):
            with guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):send(handler,port)
            self.assertEqual(handler.readTxRx(port,1,40,1),([1],0,0))
            self.assertEqual(guard.writes,1)
            self.assertEqual(guard.reads,1)
            self.assertEqual(len(port.sent),2)

    def test_missing_installed_transmit_guard_refuses_forwarding(self):
        handler=sdk_handler();port=FakePort();guard=arm.WriteGuard("enable",lambda _:None)
        guard.port=port;guard.goal_verified=True
        with StrictIndividualReplies(handler,write_guard=guard),self.assertRaises(ReplyRejected),guard.permission("Torque_Enable",dict.fromkeys(arm.JOINTS,1)):
            send(handler,port)
        self.assertEqual(port.sent,[])


if __name__=="__main__":unittest.main()
