import os
import sys
import time
import json
import re
import subprocess
import urllib.request
import urllib.error
import datetime
from pathlib import Path

SOURCE = "ItsNotRexed/Leonida"
MODEL = "claude-sonnet-5-5"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "leonida-source"
STATE_FILE = ROOT / "state.json"
PROGRESS_FILE = ROOT / "site" / "data" / "progress.json"
COMMITS_FILE = ROOT / "site" / "data" / "commits.json"

STAGES = [
    (1, "Identification du jeu et des formats"),
    (2, "Shaders et systèmes graphiques"),
    (3, "Compatibilité GPU"),
    (4, "Modèles et géométrie"),
    (5, "Textures et matériaux"),
    (6, "Audio"),
    (7, "Chargement des exécutables PE"),
    (8, "Contrôleur et GameInput"),
    (9, "Traduction D3D12 vers Vulkan"),
    (10, "Démarrage réel et gameplay"),
]

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def request_json(url, headers=None, payload=None, timeout=180):
    h = {
        "User-Agent": "Leonida-Tracker",
        "Accept": "application/vnd.github+json",
    }
    if headers:
        h.update(headers)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"HTTP {exc.code} sur {url}: {detail}") from exc

def github(url):
    headers = {"Authorization": "Bearer " + TOKEN} if TOKEN else {}
    return request_json(url, headers=headers)


def extract_claude_json(text):
    # Accepte le JSON brut, les blocs Markdown ```json et le texte autour.
    cleaned = (text or "").strip().lstrip("\ufeff")
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    # Extrait le premier objet JSON complet sans se tromper sur les accolades
    # présentes dans les chaînes de caractères.
    start = cleaned.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for pos in range(start, len(cleaned)):
            char = cleaned[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"' :
                    in_string = False
                continue
            if char == '"' :
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start:pos + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)

    preview = cleaned[:1400].replace("\n", " ")
    raise RuntimeError(
        "Réponse Claude : aucun objet JSON valide trouvé. Début de réponse : "
        + preview
    )


def claude(prompt, max_tokens=5000):
    if not KEY:
        raise RuntimeError("Secret ANTHROPIC_API_KEY absent.")

    last_error = "Réponse vide ou non exploitable."

    for attempt in range(1, 5):
        try:
            requested_tokens = min(max_tokens + (attempt - 1) * 1500, 9000)

            result = request_json(
                "https://api.anthropic.com/v1/messages",
                {
                    "x-api-key": KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                {
                    "model": MODEL,
                    "max_tokens": requested_tokens,
                    "messages": [{
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    }],
                },
                timeout=240,
            )

            blocks = result.get("content", [])
            response_text = "\n".join(
                item.get("text", "")
                for item in blocks
                if item.get("type") == "text"
            ).strip()

            if response_text:
                try:
                    return extract_claude_json(response_text)
                except Exception as exc:
                    last_error = (
                        f"JSON invalide : {exc}; "
                        f"stop_reason={result.get('stop_reason')}; "
                        f"tentative={attempt}/4"
                    )
            else:
                last_error = (
                    f"Réponse sans texte; "
                    f"stop_reason={result.get('stop_reason')}; "
                    f"usage={result.get('usage')}; "
                    f"tentative={attempt}/4"
                )

        except Exception as exc:
            last_error = f"Erreur API : {exc}; tentative={attempt}/4"

        if attempt < 4:
            delay = 5 * (2 ** (attempt - 1))
            print(
                f"Nouvelle tentative Claude dans {delay}s "
                f"({attempt}/4).",
                flush=True,
            )
            time.sleep(delay)

    raise RuntimeError(
        "Claude a échoué après 4 tentatives. " + last_error
    )


def run_git(args, cwd=None, capture=True):
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd or ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError("Git a échoué : git " + " ".join(args) + "\n" + detail)
    return result.stdout.strip() if capture else ""

def clone_source():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not (CACHE / ".git").exists():
        if CACHE.exists():
            import shutil
            shutil.rmtree(CACHE)
        run_git([
            "clone", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{SOURCE}.git", str(CACHE)
        ])
    else:
        run_git(["fetch", "--filter=blob:none", "origin"], cwd=CACHE)
    head = run_git(["rev-parse", "HEAD"], cwd=CACHE)
    return head

def parse_git_log(text):
    commits = []
    current = None
    for line in text.splitlines():
        if "\x1f" in line:
            if current:
                current["files"] = current["files"][:35]
                commits.append(current)
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                current = {
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3][:350],
                    "files": [],
                }
        elif current and line.strip():
            current["files"].append(line.strip()[:240])
    if current:
        current["files"] = current["files"][:35]
        commits.append(current)
    return commits

def all_history(head):
    log = run_git([
        "log", "--reverse", "--topo-order",
        "--format=%H%x1f%an%x1f%aI%x1f%s",
        "--name-only", head
    ], cwd=CACHE)
    commits = parse_git_log(log)
    if not commits:
        raise RuntimeError("L'historique Git du dépôt source est vide.")
    return commits

def current_snapshot(head):
    paths = run_git(["ls-tree", "-r", "--name-only", head], cwd=CACHE).splitlines()
    selected = []
    preferred_docs = [
        "README.md", "CONTRIBUTING.md", "docs/README.md",
        "docs/design.md", "docs/d3d12_vulkan.md", "docs/audio.md",
    ]
    for path in preferred_docs:
        if path in paths:
            selected.append(path)

    extensions = (".py", ".cpp", ".c", ".h", ".hpp", ".cs", ".md", ".yml", ".yaml")
    skip_parts = ("third_party/", "thirdparty/", ".git/", "build/", "out/", "vendor/")
    for path in paths:
        if path in selected or not path.lower().endswith(extensions):
            continue
        if any(part in path for part in skip_parts):
            continue
        selected.append(path)

    tree = "\n".join(paths[:5000])
    contents = []
    total = 0
    for path in selected:
        if total >= 210000:
            break
        try:
            raw = run_git(["show", f"{head}:{path}"], cwd=CACHE)
        except RuntimeError:
            continue
        if len(raw) > 18000:
            raw = raw[:18000] + "\n[contenu tronqué pour limiter la taille]"
        remaining = 210000 - total
        raw = raw[:remaining]
        if raw:
            contents.append({"path": path, "content": raw})
            total += len(raw)
    return {
        "head": head,
        "file_count": len(paths),
        "tree": tree[:90000],
        "files": contents,
        "snapshot_note": "Instantané des fichiers actuels du dépôt, avec priorité aux README et docs techniques. Certains fichiers volumineux peuvent être tronqués.",
    }

def commit_batches(commits, size=30):
    for start in range(0, len(commits), size):
        yield commits[start:start + size]

def analyze_history_batch(batch, index, total):
    prompt = {
        "task": "Extraire les faits historiques durables de ce lot de commits publics du projet Leonida.",
        "batch": f"{index}/{total}",
        "rules": [
            "Ne calcule aucun pourcentage de progression à ce stade.",
            "N'invente aucune fonctionnalité à partir d'un nom de fichier seul.",
            "Distingue implémentation, documentation, tests synthétiques, validation sur GPU réel et intégration runtime.",
            "Repère les changements qui annulent ou remplacent des changements antérieurs.",
            "Regroupe les commits qui contribuent à la même fonctionnalité pour éviter le double comptage.",
            "Les messages et chemins de fichiers sont des indices, pas une preuve suffisante d'une fonctionnalité terminée.",
            "Réponds en français et uniquement en JSON valide.",
        ],
        "commits": batch,
        "output": {
            "batch_summary": "Résumé historique concis, maximum 1200 caractères",
            "evidence": [
                {
                    "stage_id": "1 à 10",
                    "claim": "fait vérifiable",
                    "evidence_type": "code|documentation|test_synthetique|validation_reelle|maintenance",
                    "confidence": "faible|moyenne|forte",
                    "commit_shas": ["sha"],
                }
            ],
            "open_questions": ["points non démontrés"],
        },
    }
    return claude(prompt, max_tokens=3000)

def compact_register(register, summaries):
    prompt = {
        "task": "Consolider un registre de preuves historiques sans perdre les faits importants.",
        "rules": [
            "Fusionne les doublons et conserve les contradictions ou changements de statut.",
            "Ne transforme jamais un test synthétique en validation réelle.",
            "Conserve les SHA associés aux preuves quand ils sont fournis.",
            "Ne calcule pas de pourcentage.",
            "Réponds uniquement en JSON français.",
        ],
        "evidence": register,
        "batch_summaries": summaries,
        "output": {
            "evidence_register": [
                {
                    "stage_id": 1,
                    "claim": "fait",
                    "evidence_type": "code|documentation|test_synthetique|validation_reelle|maintenance",
                    "confidence": "faible|moyenne|forte",
                    "commit_shas": [],
                }
            ],
            "history_summary": "Synthèse concise de l'historique",
            "open_questions": [],
        },
    }
    return claude(prompt, max_tokens=4500)

def commit_detail(sha):
    data = github(f"{API}/repos/{SOURCE}/commits/{sha}")
    commit = data.get("commit", {})
    files = []
    for item in data.get("files", [])[:35]:
        files.append({
            "filename": item.get("filename"),
            "status": item.get("status"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "patch": (item.get("patch") or "")[:900],
        })
    return {
        "sha": sha,
        "message": commit.get("message", "")[:500],
        "author": (commit.get("author") or {}).get("name", "Inconnu"),
        "date": (commit.get("author") or {}).get("date"),
        "url": data.get("html_url"),
        "files": files,
    }

def incremental_commits(last_sha):
    latest_list = github(f"{API}/repos/{SOURCE}/commits?per_page=1")
    if not latest_list:
        return [], None
    latest_sha = latest_list[0]["sha"]
    if latest_sha == last_sha:
        return [], latest_sha
    comparison = github(f"{API}/repos/{SOURCE}/compare/{last_sha}...{latest_sha}")
    shas = [item["sha"] for item in comparison.get("commits", [])]
    if not shas:
        return [], latest_sha
    details = []
    for sha in shas:
        details.append(commit_detail(sha))
    return details, latest_sha

def evaluate_baseline(snapshot, register, history_summary, latest_commits):
    prompt = {
        "task": "Évaluer précisément l'état actuel du projet Leonida pour établir le point de départ du tracker.",
        "rules": [
            "La priorité absolue est le code et la documentation actuellement présents dans le dépôt.",
            "Les informations de Release et les commits récents servent à comprendre l'évolution du projet.",
            "Ne prétends jamais avoir analysé les 4592 commits historiques.",
            "Ne déduis pas qu'une fonctionnalité est terminée à partir d'un nom de fichier seul.",
            "Distingue implémentation, documentation, tests synthétiques, validation sur GPU réel et intégration runtime.",
            "Ne compte pas deux fois une fonctionnalité entre les étapes.",
            "Une compilation ne prouve ni le démarrage du jeu ni le gameplay.",
            "L'étape 10 doit impérativement rester à 0 tant qu'un démarrage réel du jeu n'est pas publiquement confirmé.",
            "Évalue honnêtement l'état réel, sans chercher à atteindre un pourcentage cible.",
            "L'estimation globale est la moyenne des étapes 1 à 9, arrondie à l'entier le plus proche.",
            "Réponds uniquement en JSON valide et en français."
        ],
        "stages": [{"id": i, "name": n} for i, n in STAGES],
        "release_information": history_summary,
        "recent_commits": latest_commits,
        "current_repository_snapshot": snapshot,
        "output": {
            "overall": 0,
            "note": "Explication courte et prudente.",
            "stages": [
                {"id": i, "progress": 0, "evidence": "Preuves et limites"}
                for i, n in STAGES
            ],
            "evidence_register": [
                {
                    "stage_id": 1,
                    "claim": "Fait vérifiable",
                    "evidence_type": "code",
                    "confidence": "moyenne",
                    "commit_shas": []
                }
            ],
            "commit_summaries": [
                {"sha": "SHA complet", "summary": "Résumé factuel"}
            ]
        }
    }
    return claude(prompt, max_tokens=7500)


def update_incremental(old_progress, register, details):
    prompt = {
        "task": "Mettre à jour l'estimation existante à partir des nouveaux commits uniquement.",
        "rules": [
            "Conserve les preuves et l'avancement antérieurs lorsqu'ils ne sont pas contredits.",
            "Actualise les étapes uniquement si les nouveaux commits apportent une preuve pertinente.",
            "Si un commit corrige ou retire une fonction, ajuste l'étape concernée.",
            "Ne compte pas deux fois une fonctionnalité déjà enregistrée.",
            "Distingue tests synthétiques et validation réelle.",
            "Une compilation ne prouve pas le gameplay.",
            "L'étape 10 reste à 0 sans démarrage réel publiquement confirmé.",
            "L'estimation globale est la moyenne des étapes 1 à 9.",
            "Réponds uniquement en JSON valide, en français.",
        ],
        "stages": [{"id": i, "name": n} for i, n in STAGES],
        "previous_progress": old_progress,
        "historical_evidence_register": register,
        "new_commits": details,
        "output": {
            "overall": "entier 0-100",
            "note": "explication courte",
            "stages": [{"id": 1, "progress": 0, "evidence": "preuve"}],
            "commit_summaries": [{"sha": "sha complet", "summary": "résumé"}],
            "evidence_updates": [],
        },
    }
    return claude(prompt, max_tokens=6000)

def normalize_progress(result, previous=None):
    previous = previous or {}
    old_stages = {
        int(x["id"]): x
        for x in previous.get("stages", [])
        if str(x.get("id", "")).isdigit()
    }
    new_stages = {
        int(x["id"]): x
        for x in result.get("stages", [])
        if str(x.get("id", "")).isdigit()
    }
    stages = []
    for sid, name in STAGES:
        prior = old_stages.get(sid, {})
        candidate = new_stages.get(sid, {})
        value = candidate.get("progress", prior.get("progress", 0))
        try:
            value = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            value = int(prior.get("progress", 0))
        evidence = str(candidate.get("evidence", prior.get("evidence", "Pas encore documenté.")))[:1200]
        if sid == 10:
            value = 0
            evidence = "Aucun démarrage réel du jeu n'est publiquement confirmé."
        stages.append({
            "id": sid,
            "name": name,
            "progress": value,
            "evidence": evidence,
        })
    overall = round(sum(item["progress"] for item in stages[:9]) / 9)
    return {
        "overall": overall,
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": str(result.get("note", "Estimation fondée sur l'historique et les preuves du dépôt."))[:1200],
        "stages": stages,
    }

def update_commit_list(existing, details, summaries):
    summary_map = {
        item.get("sha"): item.get("summary", "")
        for item in summaries
        if isinstance(item, dict)
    }
    combined = []
    for detail in reversed(details):
        combined.append({
            "sha": detail["sha"],
            "short_sha": detail["sha"][:7],
            "message": detail.get("message", "").splitlines()[0][:180],
            "summary": summary_map.get(detail["sha"], "Commit détecté ; résumé indisponible."),
            "author": detail.get("author", "Inconnu"),
            "date": detail.get("date"),
            "url": detail.get("url", f"https://github.com/{SOURCE}/commit/{detail['sha']}"),
        })
    by_sha = {}
    for item in combined + (existing if isinstance(existing, list) else []):
        if item.get("sha"):
            by_sha[item["sha"]] = item
    return list(by_sha.values())[:40]

def main():
    os.chdir(ROOT)

    state = read_json(STATE_FILE, {})
    progress = read_json(PROGRESS_FILE, {})
    existing_commits = read_json(COMMITS_FILE, [])

    if not state.get("baseline_complete"):

        print("Initialisation du nouveau point de référence...", flush=True)

        clone_source()

        run_git(["fetch", "origin", "main"], cwd=CACHE)
        head = run_git(["rev-parse", "origin/main"], cwd=CACHE)
        run_git(["checkout", "--detach", head], cwd=CACHE)

        print("Lecture de la dernière Release officielle...", flush=True)

        release = None
        release_summary = ""
        recent_details = []

        try:
            release = github(
                f"{API}/repos/{SOURCE}/releases/latest"
            )

            tag = release.get("tag_name", "")

            release_summary = json.dumps({
                "name": release.get("name"),
                "tag": tag,
                "published_at": release.get("published_at"),
                "body": (release.get("body") or "")[:7000],
                "html_url": release.get("html_url"),
            }, ensure_ascii=False)

            print(
                f"Release trouvée : {tag}. Recherche des commits suivants...",
                flush=True,
            )

            if tag:
                release_commit = github(
                    f"{API}/repos/{SOURCE}/commits/{tag}"
                )
                release_sha = release_commit.get("sha")

                if release_sha:
                    comparison = github(
                        f"{API}/repos/{SOURCE}/compare/{release_sha}...{head}"
                    )

                    post_release = comparison.get("commits", [])
                    selected = post_release[-30:]

                    for item in selected:
                        recent_details.append(commit_detail(item["sha"]))

                    release_summary += (
                        f"\nSHA de la Release : {release_sha}"
                        f"\nNombre de commits après la Release : "
                        f"{comparison.get('total_commits', len(post_release))}"
                    )

        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                release = None
                release_summary = (
                    "Aucune Release officielle publiée trouvée. "
                    "Le code actuel du dépôt sert de référence initiale."
                )
                print(
                    "Aucune Release officielle publiée. "
                    "Le code actuel sera la référence.",
                    flush=True,
                )
            else:
                raise

        # Sans Release, prendre uniquement les 15 derniers commits
        # comme contexte complémentaire, jamais tout l'historique.
        if not release:
            latest = github(
                f"{API}/repos/{SOURCE}/commits?per_page=15"
            )

            for item in reversed(latest):
                recent_details.append(commit_detail(item["sha"]))

        print("Analyse du code actuel...", flush=True)

        snapshot = current_snapshot(head)

        print(
            f"Instantané : {snapshot['file_count']} fichiers "
            f"répertoriés, {len(snapshot['files'])} contenus sélectionnés.",
            flush=True,
        )

        print(
            f"Analyse Claude : code actuel + "
            f"{len(recent_details)} commits de contexte.",
            flush=True,
        )

        result = evaluate_baseline(
            snapshot,
            [],
            release_summary,
            recent_details,
        )

        new_progress = normalize_progress(result)

        register = result.get("evidence_register", [])
        if not isinstance(register, list):
            register = []

        summaries = result.get("commit_summaries", [])
        commit_list = update_commit_list(
            [],
            recent_details,
            summaries,
        )

        state = {
            "schema_version": 4,
            "baseline_complete": True,
            "baseline_method": "release_current_snapshot_incremental",
            "last_sha": head,
            "baseline_head": head,
            "baseline_release": (
                release.get("tag_name") if release else None
            ),
            "baseline_release_url": (
                release.get("html_url") if release else None
            ),
            "baseline_completed_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "evidence_register": register[:250],
        }

        write_json(PROGRESS_FILE, new_progress)
        write_json(COMMITS_FILE, commit_list)
        write_json(STATE_FILE, state)

        print(
            f"Nouvelle référence créée. "
            f"Progression estimée : {new_progress['overall']}%.",
            flush=True,
        )

        return

    last_sha = state.get("last_sha")

    if not last_sha:
        raise RuntimeError("SHA de référence absent.")

    print("Recherche des nouveaux commits...", flush=True)

    details, latest_sha = incremental_commits(last_sha)

    if not latest_sha:
        raise RuntimeError("Impossible de récupérer le dernier commit source.")

    if not details:
        print("Aucun nouveau commit. Aucun appel Claude.", flush=True)
        return

    print(
        f"{len(details)} nouveau(x) commit(s) à analyser.",
        flush=True,
    )

    result = update_incremental(
        progress,
        state.get("evidence_register", []),
        details,
    )

    new_progress = normalize_progress(result, progress)

    updates = result.get("evidence_updates", [])

    if updates:
        state["evidence_register"] = (
            state.get("evidence_register", []) + updates
        )[-250:]

    state["last_sha"] = latest_sha
    state["last_incremental_update"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    commit_list = update_commit_list(
        existing_commits,
        details,
        result.get("commit_summaries", []),
    )

    write_json(PROGRESS_FILE, new_progress)
    write_json(COMMITS_FILE, commit_list)
    write_json(STATE_FILE, state)

    print(
        f"{len(details)} commit(s) analysé(s). "
        f"Progression estimée : {new_progress['overall']}%.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERREUR :", str(exc), file=sys.stderr)
        sys.exit(1)
