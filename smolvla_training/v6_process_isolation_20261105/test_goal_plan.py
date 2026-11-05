import copy
import unittest
from goal_initialization_plan import JOINTS,CAL_HASH,create_plan

def fixture():
    return dict(status="GOAL_READ_CROSSCHECK_COMPLETE",register_writes=0,calibration_sha256=CAL_HASH,records_preserved=True,
                joints=[dict(joint=j,torque=0,mode=0,moving=0,status=0,temperature=30,load=0,present_raw=1000,goal_individual_raw=0,goal_sync_raw=0,calibration_raw_range=[100,3000]) for j in JOINTS])

class GoalPlanTests(unittest.TestCase):
    def test_proposes_current_without_execution_permission(self):
        data=fixture();before=copy.deepcopy(data);plan=create_plan(data)
        self.assertFalse(plan["execution_authorized"]);self.assertEqual(plan["register_writes"],0)
        self.assertEqual([r["proposed_goal_raw"] for r in plan["joints"]],[1000]*6)
        self.assertEqual(data,before)
    def test_on_hot_unknown_or_inconsistent_refused(self):
        for key,value in (("torque",1),("temperature",45),("mode",1),("moving",1),("status",1),("load",200),("goal_sync_raw",1),("present_raw",float("nan")),("calibration_raw_range",[1001,3000])):
            data=fixture();data["joints"][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):create_plan(data)
    def test_bad_evidence_refused(self):
        for key,value in (("register_writes",1),("calibration_sha256","unknown"),("records_preserved",False)):
            data=fixture();data[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):create_plan(data)
    def test_even_matching_goal_not_arm_authorization(self):
        data=fixture()
        for row in data["joints"]:row["goal_individual_raw"]=row["goal_sync_raw"]=1000
        result=create_plan(data)
        self.assertEqual(result["same_goal_count"],6);self.assertFalse(result["execution_authorized"])

if __name__=="__main__":unittest.main()
