"""Run the installed LeLab episode loop with in-memory Feetech endpoints."""
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from scservo_sdk.protocol_packet_handler import protocol_packet_handler
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.teleoperators.teleoperator import Teleoperator
from guarded_app import record

NAMES = ("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")
ADDR = {"Present_Position":(56,2),"Goal_Position":(42,2),"Operating_Mode":(33,1),"Torque_Enable":(40,1),
        "Max_Torque_Limit":(16,2),"Protection_Current":(28,2),"Overload_Torque":(36,1),"Acceleration":(41,1)}


class MemoryEndpoint:
    def __init__(self):
        self.is_using=False
        self.open=False
        self.writes=[]
        self.reply = b""
        self.drop_wrist_reads = 0
        self.wrist_reads = 0
        self.torque_on_writes = 0
        self.bank={(m,a):v for m in range(1,7) for a,v in [(40,0),(33,0),(56,2048),(16,500),(28,250),(36,25)]}

    def clearPort(self):
        self.reply = b""

    @property
    def is_open(self):
        return self.open

    def setPacketTimeout(self, length):
        pass

    def isPacketTimeout(self):
        return not self.reply

    def readPort(self, size):
        data, self.reply = self.reply[:size], self.reply[size:]
        return data

    def closePort(self):
        self.open=False

    def writePort(self,packet):
        assert self.open
        p=bytes(packet)
        if p[4]==3:
            self.writes.append((p[2],p[5],len(p)-7))
            self.bank[p[2],p[5]]=int.from_bytes(p[6:-1],"little")
            self.torque_on_writes += int(p[5] == 40 and p[6:-1] == bytes([1]))
        elif p[4]==0x83:
            self.writes.append((254,p[5],p[6]))
            for at in range(7,len(p)-1,p[6]+1):
                self.bank[p[at],p[5]]=int.from_bytes(p[at+1:at+1+p[6]],"little")
        else:
            assert p[4]==2
            motor, address, width = p[2], p[5], p[6]
            if (motor, address) == (4, 56):
                self.wrist_reads += 1
                if self.wrist_reads <= self.drop_wrist_reads:
                    return len(p)
            value = self.bank[motor, address].to_bytes(width, "little")
            body = bytes([motor, width + 2, 0]) + value
            self.reply = b"\xff\xff" + body + bytes([(~sum(body)) & 255])
        return len(p)


class Bus:
    def __init__(self,role):
        self.port_handler=MemoryEndpoint()
        self.sdk=protocol_packet_handler()
        self.motors={n:Motor(i+1, "sts3215", MotorNormMode.DEGREES) for i,n in enumerate(NAMES)}
        native = FeetechMotorsBus(port=role, motors=self.motors)
        native.port_handler = self.port_handler
        self.read = native.read
        self.port=role
        self.calibration=self.is_calibrated=True

    @property
    def is_connected(self):
        return self.port_handler.open

    def connect(self):
        self.port_handler.open=True

    def write(self,k,n,value,**kw):
        addr,width=ADDR[k]
        self.sdk.writeTxOnly(self.port_handler,self.motors[n].id,addr,width,list(int(value).to_bytes(width,"little")))

    def goals(self):
        values=[v for m in range(1,7) for v in (m,0,8)]
        self.sdk.syncWriteTxOnly(self.port_handler,42,2,values,len(values))


def run(mode,tmp):
    f,l=Bus("follower"),Bus("leader")
    missing = {"wrist_drop_once": 1, "wrist_drop_twice": 2, "wrist_offline": 10}
    f.port_handler.drop_wrist_reads = missing.get(mode, 0)
    save_expected = mode in ("normal", "wrist_drop_once", "wrist_drop_twice")
    keys={n+".pos":float for n in NAMES}
    obs={k:0.0 for k in keys}
    robot=NS(name="so101_follower",bus=f,cameras={},action_features=keys,observation_features=keys,
             get_observation=lambda:dict(obs))
    if mode=="camera_failure":
        camera=MagicMock(is_connected=False)
        camera.connect.side_effect=OSError("mock camera unavailable")
        robot.cameras["wrist"]=camera
    if mode=="partial_start":
        write=f.write
        def fail_start(field,name,value,**kwargs):
            if field=="Torque_Enable" and name=="elbow_flex" and value==1:
                raise OSError("mock motor enable timeout")
            return write(field,name,value,**kwargs)
        f.write=fail_start
    robot.send_action=lambda a:(f.goals(),a)[1]
    leader=MagicMock(spec=Teleoperator); leader.bus=l; leader.get_action.return_value=obs
    events=dict(exit_early=False,stop_recording=False,rerecord_episode=False)
    storage=MagicMock()
    storage.fps=30
    def create(*args,**kwargs):
        storage.features=kwargs["features"]
        return storage
    frames=[]
    def frame(row):
        frames.append(row)
        if len(frames)==3:
            if save_expected:
                events.update(exit_early=True,_exit_early_triggered=True)
            elif mode=="sigint":
                os.kill(os.getpid(),signal.SIGINT)
            elif mode=="web_stop":
                events.update(stop_recording=True,exit_early=True)
            elif mode=="stop_in_flight":
                events.update(stop_recording=True,exit_early=True)
                f.goals()
            else:
                f.write("Acceleration","elbow_flex",254)
    storage.add_frame.side_effect=frame
    cfg=NS(robot=NS(),teleop=NS(),resume=False,display_data=False,play_sounds=False,
           dataset=NS(repo_id="local/"+mode,single_task="guard test",root=tmp,fps=30,video=False,
                      rgb_encoder=None,depth_encoder=None,encoder_queue_maxsize=2,encoder_threads=1,
                      video_encoding_batch_size=1,streaming_encoding=False,num_image_writer_processes=0,
                      num_image_writer_threads_per_camera=0,num_episodes=1,episode_time_s=3,reset_time_s=1,
                      push_to_hub=False))
    import record_guard
    def session(*args,**kw):
        return record_guard.RecordingSession(*args,**kw,report_dir=tmp)
    with (patch("lerobot.robots.make_robot_from_config",return_value=robot),
          patch("lerobot.teleoperators.make_teleoperator_from_config",return_value=leader),
          patch("lerobot.datasets.LeRobotDataset.create",side_effect=create),
          patch("record_guard.os.path.samefile",return_value=True),
          patch("record_guard.site_config",return_value={"ports":{"leader":"memory-leader","follower":"memory-follower"}}),
          patch.object(record,"RecordingSession",side_effect=session),
          patch("serial.Serial.open",side_effect=AssertionError("hardware access forbidden"))):
        try:
            record.record_with_web_events(cfg,events)
            assert save_expected or mode in ("web_stop","stop_in_flight")
        except KeyboardInterrupt:
            assert mode=="sigint"
        except PermissionError:
            assert mode=="forbidden"
        except ConnectionError as exc:
            assert mode == "wrist_offline" and "id_=4 after 3 tries" in str(exc)
        except OSError:
            assert mode in ("camera_failure","partial_start")
    storage.finalize.assert_called_once()
    assert storage.save_episode.call_count==int(save_expected)
    if mode in missing:
        assert f.port_handler.wrist_reads == min(missing[mode] + 1, 3)
        assert f.port_handler.torque_on_writes == (6 if save_expected else 0)
    result=json.loads(next(tmp.glob("*.json")).read_bytes())
    writes=f.port_handler.writes+l.port_handler.writes
    forbidden=[p for p in writes if (p[1],p[2]) not in ((40,1),(42,2))]
    assert not forbidden and not f.is_connected and not l.is_connected
    assert len(frames)==(0 if mode in ("partial_start","camera_failure","wrist_offline") else 3)
    assert result["all_off_verified"] and not result["errors"]
    assert result["cancelled_motion"] == int(mode == "stop_in_flight")
    assert sum(c["blocked"] for c in result["counts"].values())==int(mode=="forbidden")
    assert sum(c["allowed_tx"] for c in result["counts"].values())==len(writes)
    return dict(path=mode,allowed=len(writes),blocked=int(mode=="forbidden"),forbidden_tx=len(forbidden),
                saved=storage.save_episode.call_count,torque_readback=result["torque_readback"])


if __name__=="__main__":
    results=[]
    for mode in ("normal","sigint","web_stop","stop_in_flight","forbidden","camera_failure","partial_start",
                 "wrist_drop_once","wrist_drop_twice","wrist_offline"):
        with tempfile.TemporaryDirectory() as temp:
            results.append(run(mode,Path(temp)))
    print("GUARD_TEST_RESULT="+json.dumps(results))
