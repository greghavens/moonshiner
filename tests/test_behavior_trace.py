import json, pathlib, sys, unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from behavior_trace import grade, schemas_for_seed, _result
from common import load_behavior_seeds
from runtimes.behavior import BehaviorRuntime

class BehaviorTraceTests(unittest.TestCase):
    def test_exact_parallel_stage_accepts_reordered_calls(self):
        seed=next(s for s in load_behavior_seeds()
                  if len(s["expected"]["stages"]) == 1
                  and s["expected"]["stages"][0].get("parallel")
                  and len(s["expected"]["stages"][0]["calls"]) > 1)
        calls=list(reversed(seed["expected"]["stages"][0]["calls"]))
        message={"role":"assistant","tool_calls":[{"id":str(i),"type":"function",
          "function":{"name":c["tool"],"arguments":json.dumps(c["arguments"])}}
          for i,c in enumerate(calls)]}
        final={"role":"assistant","content":"Done."}
        self.assertTrue(grade(seed,[message,final])["accepted"])

    def test_forbidden_tool_rejects(self):
        seed=load_behavior_seeds()[0]; forbidden=seed["expected"]["forbidden_tools"][0]
        messages=[{"role":"assistant","tool_calls":[{"id":"1","type":"function",
          "function":{"name":forbidden,"arguments":"{}"}}]},
          {"role":"assistant","content":"Done"}]
        self.assertFalse(grade(seed,messages)["accepted"])

    def test_schema_surface_is_seed_selected(self):
        seed=load_behavior_seeds()[0]
        names={x["function"]["name"] for x in schemas_for_seed(seed)}
        self.assertEqual(names,set(seed["available_tools"]))

    def test_structured_search_filters_are_exact_not_substrings(self):
        seed=next(s for s in load_behavior_seeds()
                  if s["id"] == "behavior-dependency-planning-0002")
        result=_result(seed,"travel_search",{
            "query":"Kyoto Research Symposium Trip","location":"Kyoto"},1,
            json.loads(json.dumps(seed["initial_state"])))
        self.assertEqual([x["id"] for x in result["records"]],["tra-102"])
