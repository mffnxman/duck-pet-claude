"""
Duck Brain — searches Obsidian vault + claude-mem for relevant context.
Two knowledge sources:
  1. Obsidian vault (markdown notes)
  2. claude-mem SQLite DB (observations + session summaries from past sessions)
"""
import os
import re
import sqlite3
import json
from collections import defaultdict

# Load vault path from config, or scan for first Obsidian vault
_BASE = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.path.join(_BASE, "config.json")

def _find_vault():
    """Find Obsidian vault path from config or auto-detect."""
    if os.path.isfile(_CONFIG):
        try:
            with open(_CONFIG) as f:
                cfg = json.load(f)
            if "obsidian_vault" in cfg:
                return os.path.expanduser(cfg["obsidian_vault"])
        except (json.JSONDecodeError, OSError):
            pass
    # Auto-detect: look for common Obsidian locations
    for candidate in [r"~\Obsidian", r"~\Documents\Obsidian", r"~\Notes"]:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            # Use first subfolder that has .md files
            for d in os.listdir(path):
                sub = os.path.join(path, d)
                if os.path.isdir(sub) and any(f.endswith(".md") for f in os.listdir(sub)):
                    return sub
    return ""

VAULT_PATH = _find_vault()
CLAUDE_MEM_DB = os.path.expanduser(r"~\.claude-mem\claude-mem.db")


# ── Obsidian vault search ──────────────────────────────

def _load_vault():
    """Load all markdown files from the vault, split into sections."""
    sections = []
    vault = VAULT_PATH
    if not os.path.isdir(vault):
        return sections

    for fname in os.listdir(vault):
        if not fname.endswith(".md") or fname == "MEMORY.md":
            continue
        path = os.path.join(vault, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        # Split by headings into chunks
        chunks = re.split(r"(?=^#{1,3} )", content, flags=re.MULTILINE)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk or len(chunk) < 20:
                continue
            sections.append({
                "file": fname,
                "text": chunk,
            })

    return sections


def _search_vault(query_words, max_results=3, max_chars=1200):
    """Search Obsidian vault with TF-IDF-style keyword matching."""
    sections = _load_vault()
    if not sections or not query_words:
        return ""

    word_doc_count = defaultdict(int)
    for sec in sections:
        text_lower = sec["text"].lower()
        for w in query_words:
            if w in text_lower:
                word_doc_count[w] += 1

    total = len(sections)
    scored = []
    for sec in sections:
        text_lower = sec["text"].lower()
        score = 0
        for w in query_words:
            if w in text_lower:
                doc_freq = word_doc_count[w]
                idf = total / max(doc_freq, 1)
                count = text_lower.count(w)
                score += count * idf
        if score > 0:
            scored.append((score, sec))

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_results]
    if not top:
        return ""

    parts = []
    chars = 0
    for _, sec in top:
        snippet = sec["text"]
        if chars + len(snippet) > max_chars:
            snippet = snippet[:max_chars - chars] + "..."
        parts.append(f"[{sec['file']}]\n{snippet}")
        chars += len(snippet)
        if chars >= max_chars:
            break

    return "\n\n".join(parts)


# ── claude-mem search ──────────────────────────────────

def _search_memories(query, max_results=3, max_chars=1200):
    """Search claude-mem observations + session summaries via FTS."""
    if not os.path.isfile(CLAUDE_MEM_DB):
        return ""

    try:
        conn = sqlite3.connect(CLAUDE_MEM_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
    except Exception:
        return ""

    # Build FTS query — quote each word, join with OR
    words = re.findall(r"[a-z0-9]+", query.lower())
    words = [w for w in words if len(w) > 2]
    if not words:
        conn.close()
        return ""

    fts_query = " OR ".join(f'"{w}"' for w in words)
    results = []

    # Search observations
    try:
        cur.execute("""
            SELECT o.title, o.narrative, o.type,
                   rank
            FROM observations_fts fts
            JOIN observations o ON o.rowid = fts.rowid
            WHERE observations_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, max_results))
        for row in cur.fetchall():
            title = row["title"] or ""
            narrative = row["narrative"] or ""
            typ = row["type"] or ""
            text = f"[memory:{typ}] {title}\n{narrative}"
            results.append(text)
    except Exception:
        pass

    # Search session summaries
    try:
        cur.execute("""
            SELECT s.request, s.learned, s.completed,
                   rank
            FROM session_summaries_fts fts
            JOIN session_summaries s ON s.rowid = fts.rowid
            WHERE session_summaries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, max_results))
        for row in cur.fetchall():
            request = row["request"] or ""
            learned = row["learned"] or ""
            completed = row["completed"] or ""
            text = f"[session] {request}\nLearned: {learned}\nCompleted: {completed}"
            results.append(text)
    except Exception:
        pass

    conn.close()

    if not results:
        return ""

    # Trim to budget
    parts = []
    chars = 0
    for r in results:
        if chars + len(r) > max_chars:
            r = r[:max_chars - chars] + "..."
        parts.append(r)
        chars += len(r)
        if chars >= max_chars:
            break

    return "\n\n".join(parts)


# ── Combined search ────────────────────────────────────

STOP_WORDS = {"the", "a", "an", "is", "are", "was", "were", "what",
              "how", "who", "when", "where", "why", "do", "does",
              "can", "could", "would", "should", "i", "my", "me",
              "and", "or", "of", "in", "to", "for", "with", "on",
              "it", "this", "that", "be", "have", "has", "had"}


def search(query, max_results=3, max_chars=2000):
    """Search both Obsidian vault and claude-mem, return combined context."""
    query_words = set(re.findall(r"[a-z0-9]+", query.lower())) - STOP_WORDS
    if not query_words:
        return ""

    vault_ctx = _search_vault(query_words, max_results=max_results, max_chars=max_chars // 2)
    mem_ctx = _search_memories(query, max_results=max_results, max_chars=max_chars // 2)

    parts = []
    if vault_ctx:
        parts.append(f"=== From your notes ===\n{vault_ctx}")
    if mem_ctx:
        parts.append(f"=== From past sessions ===\n{mem_ctx}")

    return "\n\n---\n\n".join(parts)
