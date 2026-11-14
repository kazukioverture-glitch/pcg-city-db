"""Read-only Top8 matrix for メガドリュウズex + メタング. Never mutate canonical DB."""
import argparse,csv,hashlib,json,re
from pathlib import Path
from collections import Counter,defaultdict
from datetime import date

PRINT=re.compile(r"[A-Z][A-Za-z0-9-]*[0-9]+/[A-Za-z0-9-]+$")
FOUR=("メガドリュウズex","モグリュー","メタング","ダンバル")
TYPES=("ポケモン","グッズ","ポケモンのどうぐ","サポート","スタジアム","エネルギー")

def normalize(name):
    if not isinstance(name,str) or not name.strip(): raise ValueError("Empty card name")
    return PRINT.sub("",name).strip()

def parse_deck(deck):
    if deck.get("total_cards")!=60 or not isinstance(deck.get("cards"),list):
        raise ValueError("Missing or invalid 60-card declaration")
    counts=Counter(); sections=defaultdict(set); ids=defaultdict(set); originals=defaultdict(set)
    for c in deck["cards"]:
        if type(c.get("count")) is not int or c["count"]<=0: raise ValueError("Invalid count")
        n=normalize(c.get("name")); section=str(c.get("section","")).split(" (",1)[0]
        if section not in TYPES: raise ValueError("Unknown card category")
        counts[n]+=c["count"]; sections[n].add(section)
        ids[n].add(str(c.get("card_id") or "")); originals[n].add(c["name"])
    if sum(counts.values())!=60: raise ValueError("Card count sum != 60")
    if any(len(v)!=1 for v in sections.values()): raise ValueError("Category collision")
    return counts,{k:next(iter(v)) for k,v in sections.items()},ids,originals

def group(c):
    mega,drilbur,metang,beldum=(c.get(n,0) for n in FOUR)
    if mega<1 or metang<1: return "OUT"
    if drilbur>=2 and metang>=2 and beldum>=2:
        return "A" if mega>=2 else "B"
    return "C"

def evaluate(city,deckdoc,index):
    if city.get("season")!="2027-S1" or index.get("season")!="2027-S1":
        raise ValueError("Season mismatch")
    decks=deckdoc["decks"]
    rows=[]; exceptions=[]; missing=[]; invalid=[]; seen=set()
    metas={}; allids=defaultdict(set); allraw=defaultdict(set)
    examined=0
    for event in city["events"]:
        for i,p in enumerate(event.get("placements",[])):
            rank=p.get("rank")
            if not str(rank).isdigit() or not 1<=int(rank)<=8: continue
            rank=int(rank); examined+=1
            rowid=str(event["event_id"])+":"+str(i)
            if rowid in seen: raise ValueError("Duplicate placement "+rowid)
            seen.add(rowid)
            code=p.get("deck_code")
            if not code or code not in decks:
                missing.append(rowid); continue
            try: counts,sections,ids,names=parse_deck(decks[code])
            except ValueError as e:
                invalid.append({"row_id":rowid,"error":str(e)}); continue
            kind=group(counts)
            if kind=="OUT": continue
            d=date.fromisoformat(event["date"])
            if d<date(2026,9,21): raise ValueError("Outside 2027 S1 calendar")
            row={"row_id":rowid,"event_id":str(event["event_id"]),"date":event["date"],
                 "week":f"W{(d-date(2026,9,21)).days//7+1:02d}",
                 "prefecture":event.get("venue_prefecture") or "",
                 "category":event.get("category") or "",
                 "rank":rank,"rank_label":{1:"優勝",2:"準優勝",3:"ベスト4",5:"ベスト8"}.get(rank,str(rank)),
                 "deck_code":code,"url":p.get("deck_url") or decks[code].get("deck_url") or
                   "https://www.pokemon-card.com/deck/confirm.html/deckID/"+code,
                 "group":kind,"counts":dict(counts)}
            for n,section in sections.items():
                if n in metas and metas[n]!=section: raise ValueError("Category mismatch for "+n)
                metas[n]=section
                allids[n].update(ids[n]); allraw[n].update(names[n])
            (rows if kind in ("A","B") else exceptions).append(row)
    rows.sort(key=lambda r:(r["date"],r["event_id"],r["rank"],r["row_id"]))
    exceptions.sort(key=lambda r:(r["date"],r["event_id"],r["rank"],r["row_id"]))
    if not rows: raise ValueError("No A/B qualifying Top8 placements")
    cards=[]
    for n in sorted({n for r in rows for n in r["counts"]},key=lambda n:(TYPES.index(metas[n]),n)):
        counts=[r["counts"].get(n,0) for r in rows]; size=len(counts)
        a=[r["counts"].get(n,0) for r in rows if r["group"]=="A"]
        b=[r["counts"].get(n,0) for r in rows if r["group"]=="B"]
        histogram=Counter(counts); modal=max(histogram.values())
        cards.append({"name":n,"category":metas[n],
                      "adopted":sum(x>0 for x in counts),
                      "adoption_rate":round(sum(x>0 for x in counts)/size,6),
                      "minimum":min(counts),
                      "floor90":sorted(counts,reverse=True)[(9*size+9)//10-1],
                      "mean":round(sum(counts)/size,6),
                      "mode":min(x for x,v in histogram.items() if v==modal),
                      "maximum":max(counts),
                      "minimum_A":min(a) if a else None,"minimum_B":min(b) if b else None,
                      "distribution":{str(i):histogram[i] for i in range(max(counts)+1)},
                      "card_ids":sorted(allids[n]),"raw_names":sorted(allraw[n]),
                      "multiple_card_ids":len(allids[n])>1,
                      "condition_guarantee":n in FOUR})
    qa={"top8_rows_examined":examined,"missing_code_or_list":missing,"invalid_60":invalid,
        "main_count":len(rows),"A_count":sum(r["group"]=="A" for r in rows),
        "B_count":sum(r["group"]=="B" for r in rows),"C_count":len(exceptions),
        "fixed_100_total":sum(s["minimum"] for s in cards),
        "floor90_total":sum(s["floor90"] for s in cards),
        "all_rows_valid_60":all(sum(r["counts"].values())==60 for r in rows+exceptions),
        "unique_placement_rows":len({r["row_id"] for r in rows+exceptions})==len(rows)+len(exceptions),
        "min_floor90_max_consistent":all(s["minimum"]<=s["floor90"]<=s["maximum"] for s in cards)}
    if not(qa["all_rows_valid_60"] and qa["unique_placement_rows"] and qa["min_floor90_max_consistent"]):
        raise ValueError("QA invariant failed")
    return {"schema":"mega-excadrill-metang-top8-v1",
            "conditions":{"A":"mega>=2,drilbur>=2,metang>=2,beldum>=2",
                          "B":"mega==1,drilbur>=2,metang>=2,beldum>=2",
                          "C":"mega>=1,metang>=1,not A/B",
                          "main":"A+B","top8":"rank 1 to 8 inclusive"},
            "provenance":{"updated_at":index.get("source_updated_at"),
                         "city_sha256":index.get("city_sha256"),
                         "decks_sha256":index.get("decks_sha256"),
                         "source_url":"https://github.com/kazukioverture-glitch/pcg-city-db"},
            "qa":qa,"cards":cards,"rows":rows,"exceptions":exceptions}

def write_csv(path,header,rows):
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f); w.writerow(header); w.writerows(rows)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--city",default="data/city_db.json")
    ap.add_argument("--decks",default="data/city_decks.json")
    ap.add_argument("--index",default="data/index.json")
    ap.add_argument("--output",default=".tmp/mega-excadrill-matrix")
    args=ap.parse_args()
    idx=json.loads(Path(args.index).read_text(encoding="utf-8"))
    cb=Path(args.city).read_bytes(); db=Path(args.decks).read_bytes()
    if hashlib.sha256(cb).hexdigest()!=idx["city_sha256"]: raise ValueError("City hash mismatch")
    if hashlib.sha256(db).hexdigest()!=idx["decks_sha256"]: raise ValueError("Deck hash mismatch")
    report=evaluate(json.loads(cb),json.loads(db),idx)
    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ids=[f"D{i+1:03d}" for i in range(len(report["rows"]))]
    cols=["category","name","adopted","adoption_rate","minimum","floor90","mean","mode",
          "maximum","minimum_A","minimum_B","condition_guarantee"]
    write_csv(out/"matrix.csv",["区分","カード名","採用件数","採用率","最低枚数","90%共通枚数",
        "平均枚数","最頻枚数","最大枚数","A最低","B最低","条件由来"]+ids,
        [[s[k] for k in cols]+[r["counts"].get(s["name"],0) for r in report["rows"]] for s in report["cards"]])
    write_csv(out/"decks.csv",["ID","日付","週","都道府県","カテゴリ","順位","型","コード","公式URL"],
        [[ids[i],r["date"],r["week"],r["prefecture"],r["category"],r["rank_label"],
          r["group"],r["deck_code"],r["url"]] for i,r in enumerate(report["rows"])])
    write_csv(out/"exceptions.csv",["日付","順位","分類","デッキコード","メガ","モグリュー","メタング","ダンバル","公式URL"],
        [[r["date"],r["rank_label"],r["group"],r["deck_code"]]+
         [r["counts"].get(n,0) for n in FOUR]+[r["url"]] for r in report["exceptions"]])
    print(json.dumps({"qa":report["qa"],"provenance":report["provenance"]},ensure_ascii=False))

if __name__=="__main__": main()
