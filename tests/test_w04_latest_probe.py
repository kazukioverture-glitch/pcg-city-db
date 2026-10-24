"""Temporary W04 classifier coverage probe against latest main data."""
import json
import unittest
from analysis.cards import section_type
from analysis.state import ROOT, read_state
from analysis.weekly import _classification_resolver

class W04LatestProbe(unittest.TestCase):
    def test_probe(self):
        city=json.loads((ROOT/"data/city_db.json").read_text(encoding="utf-8"))
        decks=json.loads((ROOT/"data/city_decks.json").read_text(encoding="utf-8"))["decks"]
        index=json.loads((ROOT/"data/index.json").read_text(encoding="utf-8"))
        sidecar=read_state(ROOT/"data/analysis/deck_classifications.json")
        resolve=_classification_resolver(sidecar,decks,index["source_updated_at"])
        rows=[]
        for e in city["events"]:
            if "2026-10-12" <= e.get("date","") <= "2026-10-18":
                for p in e.get("placements",[]):
                    if 1 <= p.get("rank",100) <= 8 and p.get("deck_code"):
                        rows.append((e,p))
        unresolved=[]
        for e,p in rows:
            code=p["deck_code"]; c=resolve(code)
            if c["parent"]: continue
            d=decks.get(code)
            unresolved.append({
                "deck_code":code,"event_id":e.get("event_id"),"date":e.get("date"),
                "category":e.get("category"),"rank":p.get("rank"),
                "pokemon":[] if not d else [
                    {"name":x.get("name"),"card_id":x.get("card_id"),"count":x.get("count")}
                    for x in d.get("cards",[]) if section_type(x.get("section"))=="pokemon"
                ]
            })
        print("W04_LATEST_PROBE "+json.dumps({
            "source_updated_at":index.get("source_updated_at"),
            "top8_rows":len(rows),
            "classified":len(rows)-len(unresolved),
            "unresolved_count":len(unresolved),
            "unresolved":unresolved,
        },ensure_ascii=False,separators=(",",":")))
        self.assertGreater(len(rows),0)
if __name__=="__main__":
    unittest.main()
