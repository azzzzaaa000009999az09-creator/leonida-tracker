import os,json,urllib.request,datetime,re,sys
SOURCE="ItsNotRexed/Leonida"
MODEL="claude-sonnet-5-5"
API="https://api.github.com"
TOKEN=os.environ.get("GITHUB_TOKEN","")
KEY=os.environ.get("ANTHROPIC_API_KEY","")
STAGES=[(1,"Identification du jeu et des formats"),(2,"Shaders et systèmes graphiques"),(3,"Compatibilité GPU"),(4,"Modèles et géométrie"),(5,"Textures et matériaux"),(6,"Audio"),(7,"Chargement des exécutables PE"),(8,"Contrôleur et GameInput"),(9,"Traduction D3D12 vers Vulkan"),(10,"Démarrage réel et gameplay")]
def read(path,default):
 try:
  with open(path,encoding="utf-8-sig") as f:return json.load(f)
 except (OSError,ValueError):return default
def write(path,data):
 with open(path,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
def api(url,headers=None,payload=None):
 h={"User-Agent":"Leonida-Tracker","Accept":"application/vnd.github+json"}
 if headers:h.update(headers)
 body=json.dumps(payload).encode() if payload is not None else None
 req=urllib.request.Request(url,data=body,headers=h)
 with urllib.request.urlopen(req,timeout=60) as r:return json.loads(r.read().decode())
def gh(url):return api(url,{"Authorization":"Bearer "+TOKEN} if TOKEN else None)
def ask_claude(commits,old):
 if not KEY:raise RuntimeError("Secret ANTHROPIC_API_KEY absent.")
 prompt={"instructions":["Analyse uniquement les données fournies.","N'invente aucune fonctionnalité.","Distingue code ajouté, intégré, tests synthétiques et validation réelle.","Ne compte pas deux fois une même fonctionnalité.","Une compilation réussie ne prouve pas le gameplay.","L'étape 10 doit rester à 0 : aucun démarrage réel n'est confirmé par ce tracker.","Réponds uniquement en JSON valide, en français."],"stages":[{"id":i,"name":n} for i,n in STAGES],"previous":old,"commits":commits,"format":{"overall":"entier 0-100","note":"explication courte","stages":[{"id":1,"progress":0,"evidence":"preuve concrète"}],"commit_summaries":[{"sha":"sha complet","summary":"résumé factuel"}]}}
 result=api("https://api.anthropic.com/v1/messages",{"x-api-key":KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},{"model":MODEL,"max_tokens":5000,"messages":[{"role":"user","content":json.dumps(prompt,ensure_ascii=False)}]})
 content="".join(x.get("text","") for x in result.get("content",[]) if x.get("type")=="text")
 match=re.search(r"\{.*\}",content,re.S)
 if not match:raise RuntimeError("Réponse Claude non reconnue.")
 return json.loads(match.group(0))
def main():
 os.chdir(os.path.dirname(os.path.abspath(__file__)))
 state=read("state.json",{"last_sha":None});progress=read("site/data/progress.json",{});old=read("site/data/commits.json",[])
 latest=gh(f"{API}/repos/{SOURCE}/commits?per_page=30")
 if not latest:print("Aucun commit.");return
 last=state.get("last_sha");fresh=[]
 for item in latest:
  if item.get("sha")==last:break
  fresh.append(item)
 if not fresh:print("Aucun nouveau commit.");return
 if last is None:fresh=fresh[:5]
 fresh=list(reversed(fresh[:5]));details=[]
 for item in fresh:
  d=gh(f"{API}/repos/{SOURCE}/commits/{item['sha']}")
  cm=d.get("commit",{});files=[]
  for f in d.get("files",[])[:30]:
   files.append({"filename":f.get("filename"),"status":f.get("status"),"additions":f.get("additions"),"deletions":f.get("deletions"),"patch":(f.get("patch") or "")[:1400]})
  details.append({"sha":item["sha"],"message":cm.get("message",""),"author":(cm.get("author") or {}).get("name","Inconnu"),"date":(cm.get("author") or {}).get("date"),"url":d.get("html_url"),"files":files})
 result=ask_claude(details,progress)
 old_stages={int(x["id"]):x for x in progress.get("stages",[]) if str(x.get("id","")).isdigit()}
 new_stages={int(x["id"]):x for x in result.get("stages",[]) if str(x.get("id","")).isdigit()}
 stages=[]
 for sid,name in STAGES:
  previous=old_stages.get(sid,{});candidate=new_stages.get(sid,{})
  value=max(0,min(100,int(candidate.get("progress",previous.get("progress",0)))))
  evidence=str(candidate.get("evidence",previous.get("evidence","Pas encore documenté.")))[:700]
  if sid==10:value=0;evidence="Aucun démarrage réel du jeu n'est confirmé."
  stages.append({"id":sid,"name":name,"progress":value,"evidence":evidence})
 overall=round(sum(x["progress"] for x in stages[:9])/9)
 progress={"overall":overall,"last_updated":datetime.datetime.now(datetime.timezone.utc).isoformat(),"note":str(result.get("note","Estimation fondée sur les commits publics."))[:700],"stages":stages}
 summaries={x.get("sha"):x.get("summary","") for x in result.get("commit_summaries",[])}
 for d in reversed(details):
  old.insert(0,{"sha":d["sha"],"short_sha":d["sha"][:7],"message":d["message"].splitlines()[0][:180],"summary":summaries.get(d["sha"],"Commit détecté ; résumé IA indisponible."),"author":d["author"],"date":d["date"],"url":d["url"]})
 unique={}
 for c in old:unique[c.get("sha")]=c
 write("site/data/progress.json",progress);write("site/data/commits.json",list(unique.values())[:40]);write("state.json",{"last_sha":latest[0]["sha"]})
 print(f"{len(details)} commit(s) analysé(s). Progression estimée : {overall}%.")
if __name__=="__main__":
 try:main()
 except Exception as e:print("ERREUR :",str(e),file=sys.stderr);sys.exit(1)