"""
knowledge_graph.py — linked memory, so the assistant knows how facts relate.

WHAT THIS ADDS OVER save_memory / recall_memory
    The memory store is a flat dictionary: `sister_name = Ayşe`, `city =
    Istanbul`. That answers "who is Ayşe?" but not "who lives in Istanbul?"
    or "what do I know about Ayşe?" — because nothing records that the sister
    fact and the city fact belong to the same person.

    This skill adds EDGES between facts, in a plain JSON file beside the existing
    store. A node is a name; an edge says two nodes are related and how. Then:

        link("Ayşe", "sister of", "Manoj")
        link("Ayşe", "lives in", "Istanbul")

    makes "who lives in Istanbul?" answerable by walking the graph, and
    "everything I know about Ayşe" answerable in one lookup.

STORAGE
    memory/knowledge_graph.json — a plain adjacency structure. Human-readable,
    on the user's machine, deleted along with the rest of their data. No server,
    matching the promise the rest of the app keeps.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()


def _store_path() -> Path:
    from core.paths import app_root
    return app_root() / "memory" / "knowledge_graph.json"


def _load() -> dict:
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "edges" in data:
            return data
    except Exception:
        pass
    return {"edges": [], "updated": ""}


def _save(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _norm(name: str) -> str:
    return " ".join(str(name or "").split()).strip()


def link(subject: str, relation: str, obj: str) -> str:
    """Record that `subject` is related to `obj` by `relation`."""
    subject, relation, obj = _norm(subject), _norm(relation), _norm(obj)
    if not (subject and relation and obj):
        return "I need a subject, a relation and an object."

    with _LOCK:
        data = _load()
        edges = data["edges"]
        for e in edges:
            if (e["subject"].lower() == subject.lower()
                    and e["relation"].lower() == relation.lower()
                    and e["object"].lower() == obj.lower()):
                return f"Already known: {subject} {relation} {obj}."
        edges.append({
            "subject": subject, "relation": relation, "object": obj,
            "since": datetime.now().strftime("%Y-%m-%d"),
        })
        _save(data)
    return f"Noted: {subject} {relation} {obj}."


def unlink(subject: str, obj: str = "") -> str:
    """Remove an edge (or every edge touching a node)."""
    subject = _norm(subject)
    obj = _norm(obj)
    with _LOCK:
        data = _load()
        before = len(data["edges"])
        if obj:
            data["edges"] = [e for e in data["edges"]
                             if not (e["subject"].lower() == subject.lower()
                                     and e["object"].lower() == obj.lower())]
        else:
            data["edges"] = [e for e in data["edges"]
                             if subject.lower() not in (e["subject"].lower(),
                                                        e["object"].lower())]
        removed = before - len(data["edges"])
        _save(data)
    return (f"Forgot {removed} connection(s) about {subject}."
            if removed else f"I had nothing recorded about {subject}.")


def about(node: str) -> str:
    """Everything directly connected to a node, in both directions."""
    node = _norm(node)
    if not node:
        return "Which name should I look up?"
    with _LOCK:
        edges = _load()["edges"]

    low = node.lower()
    outgoing = [e for e in edges if e["subject"].lower() == low]
    incoming = [e for e in edges if e["object"].lower() == low]
    if not (outgoing or incoming):
        return f"Nothing recorded about {node}."

    lines = [f"About {node}:"]
    for e in outgoing:
        lines.append(f"  • {e['subject']} {e['relation']} {e['object']}")
    for e in incoming:
        lines.append(f"  • {e['object']} {e['relation']} {e['subject']}"
                     f"  (reverse: {e['subject']})")
    return "\n".join(lines)


def connected_to(node: str, relation: str = "") -> str:
    """Walk one hop out along a given relation — 'who lives in Istanbul?'."""
    node = _norm(node)
    rel = _norm(relation).lower()
    with _LOCK:
        edges = _load()["edges"]

    low = node.lower()
    found = []
    for e in edges:
        if rel and e["relation"].lower() != rel:
            continue
        if e["subject"].lower() == low:
            found.append(f"{e['object']} ({e['relation']})")
        elif e["object"].lower() == low:
            found.append(f"{e['subject']} ({e['relation']})")
    if not found:
        return f"Nothing connected to {node}" + (f" by '{relation}'." if relation else ".")
    return f"Connected to {node}:\n" + "\n".join(f"  • {f}" for f in found)


def path_between(a: str, b: str, max_depth: int = 4) -> str:
    """How two names are related — a breadth-first search over the edges."""
    a, b = _norm(a), _norm(b)
    if not (a and b):
        return "I need two names to connect."
    with _LOCK:
        edges = _load()["edges"]

    adjacency: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        s, o = e["subject"].lower(), e["object"].lower()
        adjacency.setdefault(s, []).append((o, e["relation"]))
        adjacency.setdefault(o, []).append((s, e["relation"]))

    target = b.lower()
    queue = [(a.lower(), [a])]
    seen = {a.lower()}
    while queue:
        node, trail = queue.pop(0)
        if len(trail) > max_depth:
            break
        for neighbour, relation in adjacency.get(node, []):
            if neighbour == target:
                return " → ".join(trail + [f"[{relation}]", b])
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, trail + [f"[{relation}]", neighbour]))
    return f"I cannot connect {a} to {b} within {max_depth} steps."


def overview(limit: int = 40) -> str:
    """The whole graph, most connected names first."""
    with _LOCK:
        edges = _load()["edges"]
    if not edges:
        return "No connections recorded yet."

    degree: dict[str, int] = {}
    for e in edges:
        degree[e["subject"]] = degree.get(e["subject"], 0) + 1
        degree[e["object"]] = degree.get(e["object"], 0) + 1

    top = sorted(degree.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    lines = [f"{len(edges)} connection(s), {len(degree)} name(s).", ""]
    lines += [f"  • {name} — {count} link(s)" for name, count in top]

    # A suggested next hop is genuinely useful and costs nothing to compute.
    if top:
        name = top[0][0]
        rel = next((e["relation"] for e in edges
                    if e["subject"].lower() == name.lower()), "")
        if rel:
            others = connected_to(name, rel)
            lines += ["", f"Your best-connected context: {others.splitlines()[0]}"]
    return "\n".join(lines)


SKILL = {
    "name": "knowledge_graph",
    "description": (
        "Remembers how people, places and things relate to each other, so you can "
        "answer 'who lives in X', 'how are A and B connected', 'what do I know "
        "about A'. Use action='link' when the user reveals a relationship "
        "(e.g. 'my sister Ayşe lives in Istanbul' -> link Ayşe 'lives in' "
        "Istanbul, and Ayşe 'sister of' Manoj). Use action='about' to recall "
        "everything about one name, 'connected' to look one hop out, 'path' to "
        "relate two names, 'overview' for the whole picture, and 'forget' to "
        "remove something. Save links silently, like save_memory."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "link | about | connected | path | overview | forget",
            },
            "subject": {
                "type": "STRING",
                "description": "The main name (for link/about/connected/path/forget)",
            },
            "relation": {
                "type": "STRING",
                "description": "How they relate, e.g. 'sister of', 'lives in', 'works at', 'owns'",
            },
            "object": {
                "type": "STRING",
                "description": "The second name (for link), or the second name for path",
            },
        },
        "required": ["action"],
    },
    "tags": ["memory", "knowledge"],
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()
    subject = p.get("subject", "")
    relation = p.get("relation", "")
    obj = p.get("object", "")

    if action == "link":
        return link(subject, relation, obj)
    if action == "about":
        return about(subject)
    if action == "connected":
        return connected_to(subject, relation)
    if action == "path":
        return path_between(subject, obj or relation)
    if action == "overview":
        return overview()
    if action == "forget":
        return unlink(subject, obj)
    return ("Unknown action. Use link, about, connected, path, overview or forget.")
