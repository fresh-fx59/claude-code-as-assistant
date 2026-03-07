"""Persistent memory system for the Telegram Claude bot.

Global memory stored as:
  {MEMORY_DIR}/user_profile.yaml  — core profile + semantic facts (Claude edits directly)
  {MEMORY_DIR}/episodes.db        — episodic memory (SQLite with FTS5)

Memory context is injected as XML before each user message. Claude updates
user_profile.yaml via its built-in file tools. Episodic memory is managed
by Python (REFLECT on /new, RECALL via FTS5 search).
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config

logger = logging.getLogger(__name__)

# ── Stop words for keyword extraction ────────────────────────
_STOP_WORDS = frozenset(
    "i me my myself we our ours ourselves you your yours yourself yourselves "
    "he him his himself she her hers herself it its itself they them their "
    "theirs themselves what which who whom this that these those am is are "
    "was were be been being have has had having do does did doing a an the "
    "and but if or because as until while of at by for with about against "
    "between through during before after above below to from up down in out "
    "on off over under again further then once here there when where why how "
    "all both each few more most other some such no nor not only own same so "
    "than too very s t can will just don should now d ll m o re ve y ain "
    "aren couldn didn doesn hadn hasn haven isn ma mightn mustn needn shan "
    "shouldn wasn weren won wouldn could would please hey hi hello yes yeah "
    "ok okay thanks thank sure".split()
)

_FACT_TYPES = (
    "identity",
    "preference",
    "workflow",
    "infrastructure",
    "communication",
    "project",
    "operation",
    "tooling",
    "schedule",
    "misc",
)

_FACT_TYPE_HINTS = {
    "identity": "Stable personal details (family, role, location, birthdays).",
    "preference": "User preferences and defaults.",
    "workflow": "Execution and delivery workflow constraints.",
    "infrastructure": "Servers, domains, ports, deployment topology.",
    "communication": "Messaging/channel behavior and language rules.",
    "project": "Project-specific goals, repositories, architecture decisions.",
    "operation": "Current operational state and runtime constraints.",
    "tooling": "Tools, providers, integrations, CLI preferences.",
    "schedule": "Timing, intervals, and recurring cadence rules.",
    "misc": "Other useful context not fitting other types.",
}

_FACT_TYPE_PATTERNS = (
    ("communication", re.compile(r"(telegram|thread|topic|channel|message|voice|chat|post)", re.IGNORECASE)),
    ("workflow", re.compile(r"(workflow|commit|push|version|restart|autonomous|validation|plan|apply)", re.IGNORECASE)),
    ("infrastructure", re.compile(r"(server|ip|port|domain|dns|cloudflare|nginx|tls|docker|contabo|monitoring|prometheus|grafana|proxy|ssh)", re.IGNORECASE)),
    ("project", re.compile(r"(repo|crossposter|aiengineerhelper|iron_lady|ila|architecture|monetization)", re.IGNORECASE)),
    ("tooling", re.compile(r"(tool|provider|codex|claude|gcloud|gmail|obsidian|syncthing|cli)", re.IGNORECASE)),
    ("schedule", re.compile(r"(daily|weekly|interval|timezone|time|date)", re.IGNORECASE)),
    ("preference", re.compile(r"(preference|default|style|language)", re.IGNORECASE)),
    ("identity", re.compile(r"(name|birthday|wife|daughter|family|employer|role|location)", re.IGNORECASE)),
    ("operation", re.compile(r"(state|status|setup|rule|requirement|constraint)", re.IGNORECASE)),
)

_FACT_TYPE_PRIORITY = {
    "workflow": 0,
    "operation": 1,
    "project": 2,
    "infrastructure": 3,
    "communication": 4,
    "preference": 5,
    "identity": 6,
    "tooling": 7,
    "schedule": 8,
    "misc": 9,
}

_PROFILE_TEMPLATE = """\
# User profile and semantic memory.
# Claude: update this file when you learn about the user.
name: null
preferences:
  communication_style: null
  timezone: null
  languages: []
fact_types:
  identity: Stable personal details (family, role, location, birthdays).
  preference: User preferences and defaults.
  workflow: Execution and delivery workflow constraints.
  infrastructure: Servers, domains, ports, deployment topology.
  communication: Messaging/channel behavior and language rules.
  project: Project-specific goals, repositories, architecture decisions.
  operation: Current operational state and runtime constraints.
  tooling: Tools, providers, integrations, CLI preferences.
  schedule: Timing, intervals, and recurring cadence rules.
  misc: Other useful context not fitting other types.
facts: []
# Each fact: {key: str, value: str, type: one of fact_types, confidence: 0.0-1.0, source: explicit|inferred, updated: YYYY-MM-DD, status: active|deleted, deleted_at: YYYY-MM-DD|null}
"""

_EPISODES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    timestamp TEXT,
    summary TEXT,
    topics TEXT,
    decisions TEXT,
    entities TEXT
);
"""

_FTS_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, topics, decisions, content=episodes, content_rowid=id
);
"""

_WORKLOG_SESSIONS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS worklog_sessions (
    id INTEGER PRIMARY KEY,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    chat_id INTEGER NOT NULL,
    message_thread_id INTEGER,
    scope_key TEXT NOT NULL,
    provider TEXT,
    session_type TEXT,
    session_id TEXT,
    topic_label TEXT,
    topic_started_at TEXT,
    repo_path TEXT,
    branch TEXT,
    summary TEXT,
    started_at TEXT NOT NULL,
    closed_at TEXT,
    last_seen_at TEXT NOT NULL
);
"""

_WORKLOG_COMMITS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS worklog_commits (
    id INTEGER PRIMARY KEY,
    worklog_session_id INTEGER NOT NULL REFERENCES worklog_sessions(id) ON DELETE CASCADE,
    commit_sha TEXT NOT NULL,
    short_sha TEXT,
    subject TEXT,
    repo_path TEXT,
    branch TEXT,
    authored_at TEXT,
    committed_at TEXT,
    UNIQUE(worklog_session_id, commit_sha)
);
"""

_WORKLOG_FILES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS worklog_files (
    id INTEGER PRIMARY KEY,
    worklog_commit_id INTEGER NOT NULL REFERENCES worklog_commits(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    additions INTEGER,
    deletions INTEGER,
    UNIQUE(worklog_commit_id, path)
);
"""

_WORKLOG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_worklog_sessions_scope ON worklog_sessions(scope_key, session_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_worklog_sessions_episode ON worklog_sessions(episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_worklog_commits_session ON worklog_commits(worklog_session_id, committed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_worklog_files_commit ON worklog_files(worklog_commit_id)",
]

# Triggers to keep FTS index in sync with episodes table
_FTS_TRIGGERS = [
    """\
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, summary, topics, decisions)
    VALUES (new.id, new.summary, new.topics, new.decisions);
END;
""",
    """\
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary, topics, decisions)
    VALUES ('delete', old.id, old.summary, old.topics, old.decisions);
END;
""",
    """\
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary, topics, decisions)
    VALUES ('delete', old.id, old.summary, old.topics, old.decisions);
    INSERT INTO episodes_fts(rowid, summary, topics, decisions)
    VALUES (new.id, new.summary, new.topics, new.decisions);
END;
""",
]


class MemoryManager:
    """Global memory manager with YAML profile + SQLite episodic storage."""

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._profile_path = self._dir / "user_profile.yaml"
        self._db_path = self._dir / "episodes.db"

        # Seed profile template if missing
        if not self._profile_path.exists():
            self._profile_path.write_text(_PROFILE_TEMPLATE, encoding="utf-8")

        self._ensure_profile_schema()

        # Init SQLite
        self._init_db()

    def _init_db(self) -> None:
        """Create episodes table and FTS5 index if they don't exist."""
        con = self._connect()
        try:
            con.execute(_EPISODES_SCHEMA)
            con.execute(_FTS_SCHEMA)
            con.execute(_WORKLOG_SESSIONS_SCHEMA)
            con.execute(_WORKLOG_COMMITS_SCHEMA)
            con.execute(_WORKLOG_FILES_SCHEMA)
            for trigger in _FTS_TRIGGERS:
                con.execute(trigger)
            for statement in _WORKLOG_INDEXES:
                con.execute(statement)
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _ensure_storage(self) -> None:
        """Recreate storage if external cleanup removed the directory or DB file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        if not self._db_path.exists():
            self._init_db()

    def _ensure_profile_schema(self) -> None:
        """Normalize profile format so facts always have a memory type."""
        data = self._load_profile()
        normalized, changed = self._normalize_profile(data)
        if changed:
            self._profile_path.parent.mkdir(parents=True, exist_ok=True)
            self._profile_path.write_text(
                yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

    def _save_profile(self, data: dict) -> None:
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        self._profile_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _load_profile(self) -> dict:
        try:
            return yaml.safe_load(self._profile_path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.debug("Could not read user_profile.yaml")
            return {}

    def _normalize_profile(self, data: dict) -> tuple[dict, bool]:
        changed = False
        normalized = dict(data or {})

        if not isinstance(normalized.get("preferences"), dict):
            normalized["preferences"] = {}
            changed = True
        prefs = normalized["preferences"]
        if "communication_style" not in prefs:
            prefs["communication_style"] = None
            changed = True
        if "timezone" not in prefs:
            prefs["timezone"] = None
            changed = True
        if not isinstance(prefs.get("languages"), list):
            prefs["languages"] = []
            changed = True

        if not isinstance(normalized.get("fact_types"), dict):
            normalized["fact_types"] = dict(_FACT_TYPE_HINTS)
            changed = True

        raw_facts = normalized.get("facts")
        if not isinstance(raw_facts, list):
            raw_facts = []
            normalized["facts"] = raw_facts
            changed = True

        normalized_facts: list[dict] = []
        for entry in raw_facts:
            if not isinstance(entry, dict):
                changed = True
                continue
            key = str(entry.get("key", "")).strip()
            if not key:
                changed = True
                continue
            value = str(entry.get("value", "")).strip()
            fact_type = str(entry.get("type", "")).strip().lower()
            if fact_type not in _FACT_TYPES:
                fact_type = self._infer_fact_type(key, value)
                changed = True
            confidence = self._normalize_confidence(entry.get("confidence", 1.0))
            if confidence != entry.get("confidence", 1.0):
                changed = True
            source = entry.get("source")
            if source not in {"explicit", "inferred"}:
                source = "inferred"
                changed = True
            updated = str(entry.get("updated") or self._today_utc())
            if updated != entry.get("updated"):
                changed = True
            status = str(entry.get("status", "active")).strip().lower()
            if status not in {"active", "deleted"}:
                status = "active"
                changed = True
            deleted_at = entry.get("deleted_at")
            if status == "deleted":
                deleted_at = str(deleted_at or self._today_utc())
            else:
                deleted_at = None
            if deleted_at != entry.get("deleted_at"):
                changed = True

            normalized_facts.append(
                {
                    "key": key,
                    "value": value,
                    "type": fact_type,
                    "confidence": confidence,
                    "source": source,
                    "updated": updated,
                    "status": status,
                    "deleted_at": deleted_at,
                }
            )

        if normalized_facts != raw_facts:
            normalized["facts"] = normalized_facts
            changed = True

        return normalized, changed

    def _normalize_confidence(self, value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(1.0, round(numeric, 3)))

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode_json_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    @staticmethod
    def _is_active_fact(fact: dict) -> bool:
        return str(fact.get("status", "active")).strip().lower() != "deleted"

    def _infer_fact_type(self, key: str, value: str) -> str:
        haystack = f"{key} {value}"
        for fact_type, pattern in _FACT_TYPE_PATTERNS:
            if pattern.search(haystack):
                return fact_type
        return "misc"

    def _fact_score(self, fact: dict, keywords: list[str]) -> int:
        key = str(fact.get("key", "")).lower()
        value = str(fact.get("value", "")).lower()
        score = 0
        for token in keywords:
            if token in key:
                score += 4
            if token in value:
                score += 2
        return score

    def _select_relevant_facts(self, facts: list[dict], user_message: str, limit: int = 24) -> list[dict]:
        if not facts:
            return []
        keywords = self._extract_keywords(user_message)
        selected: list[dict] = []
        seen: set[str] = set()

        # Always keep a small anchor set.
        for fact in facts:
            if fact.get("type") not in {"identity", "preference"}:
                continue
            key = str(fact.get("key", ""))
            if key in seen:
                continue
            selected.append(fact)
            seen.add(key)
            if len(selected) >= min(limit, 6):
                break

        ranked = sorted(
            facts,
            key=lambda fact: (
                self._fact_score(fact, keywords),
                -_FACT_TYPE_PRIORITY.get(str(fact.get("type", "misc")), 99),
                float(fact.get("confidence", 0.0)),
                str(fact.get("updated", "")),
            ),
            reverse=True,
        )
        for fact in ranked:
            if len(selected) >= limit:
                break
            key = str(fact.get("key", ""))
            if key in seen:
                continue
            if keywords and self._fact_score(fact, keywords) == 0:
                continue
            selected.append(fact)
            seen.add(key)

        # If there were no keyword matches, fill with best-ranked facts.
        if len(selected) < min(limit, 10):
            for fact in ranked:
                if len(selected) >= min(limit, 10):
                    break
                key = str(fact.get("key", ""))
                if key in seen:
                    continue
                selected.append(fact)
                seen.add(key)

        return selected

    def _format_facts_by_type(self, facts: list[dict]) -> list[str]:
        buckets: dict[str, list[str]] = {}
        for fact in facts:
            fact_type = str(fact.get("type", "misc"))
            key = fact.get("key", "?")
            value = fact.get("value", "?")
            buckets.setdefault(fact_type, []).append(f"- {key}: {value}")

        lines: list[str] = []
        for fact_type in sorted(buckets, key=lambda item: _FACT_TYPE_PRIORITY.get(item, 99)):
            lines.append(f"[{fact_type}]")
            lines.extend(buckets[fact_type])
        return lines

    def list_facts(
        self,
        fact_type: str | None = None,
        min_confidence: float = 0.0,
        include_deleted: bool = False,
    ) -> list[dict]:
        """Return normalized facts optionally filtered by type and confidence."""
        raw = self._load_profile()
        data, changed = self._normalize_profile(raw)
        if changed:
            self._save_profile(data)
        facts = data.get("facts") or []
        selected = [
            fact for fact in facts
            if isinstance(fact, dict) and float(fact.get("confidence", 0.0)) >= min_confidence
        ]
        if not include_deleted:
            selected = [fact for fact in selected if self._is_active_fact(fact)]
        if fact_type:
            wanted = fact_type.strip().lower()
            selected = [fact for fact in selected if str(fact.get("type", "")).lower() == wanted]
        return selected

    def upsert_fact(
        self,
        *,
        key: str,
        value: str,
        fact_type: str | None = None,
        confidence: float = 1.0,
        source: str = "explicit",
        updated: str | None = None,
        mode: str = "replace",
    ) -> dict:
        """Create or replace a fact by key with duplicate protection."""
        clean_key = key.strip()
        if not clean_key:
            raise ValueError("Fact key cannot be empty.")
        clean_value = value.strip()
        normalized_source = source if source in {"explicit", "inferred"} else "inferred"
        final_type = (fact_type or "").strip().lower()
        if final_type not in _FACT_TYPES:
            final_type = self._infer_fact_type(clean_key, clean_value)
        normalized_mode = (mode or "replace").strip().lower()
        if normalized_mode not in {"replace", "append"}:
            raise ValueError("Unsupported mode. Use 'replace' or 'append'.")

        raw = self._load_profile()
        data, changed = self._normalize_profile(raw)
        facts = data.get("facts") or []
        target = {
            "key": clean_key,
            "value": clean_value,
            "type": final_type,
            "confidence": self._normalize_confidence(confidence),
            "source": normalized_source,
            "updated": updated or self._today_utc(),
            "status": "active",
            "deleted_at": None,
        }

        active_indexes = [
            idx
            for idx, fact in enumerate(facts)
            if str(fact.get("key", "")).strip() == clean_key and self._is_active_fact(fact)
        ]
        exact_index = next(
            (
                idx for idx in active_indexes
                if str(facts[idx].get("value", "")).strip() == clean_value
            ),
            None,
        )

        if exact_index is not None:
            facts[exact_index] = target
        elif normalized_mode == "append":
            facts.append(target)
        elif active_indexes:
            keep_idx = active_indexes[0]
            facts[keep_idx] = target
            deleted_on = self._today_utc()
            for idx in active_indexes[1:]:
                facts[idx]["status"] = "deleted"
                facts[idx]["deleted_at"] = deleted_on
                facts[idx]["updated"] = deleted_on
        else:
            facts.append(target)
        data["facts"] = facts
        self._save_profile(data)
        logger.info("Upserted memory fact key=%s type=%s mode=%s", clean_key, final_type, normalized_mode)
        if changed:
            logger.debug("Profile schema normalized while upserting fact key=%s", clean_key)
        return target

    def delete_fact(self, key: str, value: str | None = None) -> bool:
        """Soft-delete active facts by key (or by key+value)."""
        clean_key = key.strip()
        if not clean_key:
            return False
        clean_value = value.strip() if value else None
        raw = self._load_profile()
        data, changed = self._normalize_profile(raw)
        facts = data.get("facts") or []
        removed = False
        deleted_on = self._today_utc()
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            if str(fact.get("key", "")).strip() != clean_key:
                continue
            if clean_value is not None and str(fact.get("value", "")).strip() != clean_value:
                continue
            if not self._is_active_fact(fact):
                continue
            fact["status"] = "deleted"
            fact["deleted_at"] = deleted_on
            fact["updated"] = deleted_on
            removed = True
        if removed or changed:
            data["facts"] = facts
            self._save_profile(data)
        if removed:
            logger.info("Soft-deleted memory fact key=%s", clean_key)
        return removed

    def reclassify_facts(self) -> int:
        """Recompute fact types using inference rules; return changed count."""
        raw = self._load_profile()
        data, changed = self._normalize_profile(raw)
        facts = data.get("facts") or []
        updated_count = 0
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            inferred = self._infer_fact_type(str(fact.get("key", "")), str(fact.get("value", "")))
            if fact.get("type") != inferred:
                fact["type"] = inferred
                updated_count += 1
        if updated_count or changed:
            data["facts"] = facts
            self._save_profile(data)
        return updated_count

    # ── Context building ─────────────────────────────────────

    def build_context(self, user_message: str) -> str:
        """Read memory and return XML context block to prepend to the prompt.

        Returns empty string if all memory is empty/default.
        """
        sections: list[str] = []

        # Core + Semantic from YAML
        raw_data = self._load_profile()
        data, changed = self._normalize_profile(raw_data)
        if changed:
            self._save_profile(data)

        # Core profile
        core_lines: list[str] = []
        if data.get("name"):
            core_lines.append(f"Name: {data['name']}")
        prefs = data.get("preferences") or {}
        if prefs.get("communication_style"):
            core_lines.append(f"Style: {prefs['communication_style']}")
        if prefs.get("timezone"):
            core_lines.append(f"Timezone: {prefs['timezone']}")
        if prefs.get("languages"):
            core_lines.append(f"Languages: {', '.join(prefs['languages'])}")
        if core_lines:
            sections.append("<core>\n" + "\n".join(core_lines) + "\n</core>")

        # Semantic facts (confidence >= 0.6)
        facts = data.get("facts") or []
        high_conf = [
            f for f in facts
            if isinstance(f, dict)
            and self._is_active_fact(f)
            and float(f.get("confidence", 1.0)) >= 0.6
        ]
        if high_conf:
            selected = self._select_relevant_facts(high_conf, user_message, limit=24)
            lines = self._format_facts_by_type(selected)
            sections.append("<relevant_facts>\n" + "\n".join(lines) + "\n</relevant_facts>")

        # Episodic — search by keywords from user message
        episodes = self.search_episodes(user_message, limit=5)
        if episodes:
            lines = [f"- {e['timestamp'][:10]}: {e['summary']}" for e in episodes]
            sections.append("<recent_episodes>\n" + "\n".join(lines) + "\n</recent_episodes>")

        if not sections:
            return ""

        return "<memory>\n" + "\n".join(sections) + "\n</memory>"

    def build_instructions(self) -> str:
        """Return memory_instructions block with absolute file path."""
        abs_path = self._profile_path.resolve()
        return (
            "\n<memory_instructions>\n"
            f"You have persistent memory. Your profile + facts file:\n"
            f"  {abs_path}\n"
            "Update it when you learn something worth remembering about the user.\n"
            "Edit YAML directly (no bash/cat/sed/awk for memory updates).\n"
            "Use fact schema: key, value, type, confidence, source, updated, status, deleted_at.\n"
            f"Allowed fact types: {', '.join(_FACT_TYPES)}.\n"
            "Do NOT update memory on every message — only when you learn something new.\n"
            "</memory_instructions>"
        )

    # ── Episodic memory (SQLite) ─────────────────────────────

    def add_episode(
        self,
        chat_id: int,
        summary: str,
        topics: list[str] | None = None,
        decisions: list[str] | None = None,
        entities: list[str] | None = None,
        *,
        message_thread_id: int | None = None,
        scope_key: str | None = None,
        provider: str | None = None,
        session_type: str | None = None,
        session_id: str | None = None,
        topic_label: str | None = None,
        topic_started_at: str | None = None,
        repo_path: str | None = None,
        branch: str | None = None,
    ) -> int:
        """Insert a new episode into the database."""
        self._ensure_storage()
        now_iso = self._now_utc_iso()
        con = self._connect()
        try:
            cursor = con.execute(
                "INSERT INTO episodes (chat_id, timestamp, summary, topics, decisions, entities) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    now_iso,
                    summary,
                    json.dumps(topics or []),
                    json.dumps(decisions or []),
                    json.dumps(entities or []),
                ),
            )
            episode_id = int(cursor.lastrowid)
            if scope_key or session_id or provider or repo_path or branch:
                self._record_summary_link(
                    con,
                    episode_id=episode_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    scope_key=scope_key,
                    provider=provider,
                    session_type=session_type,
                    session_id=session_id,
                    topic_label=topic_label,
                    topic_started_at=topic_started_at,
                    repo_path=repo_path,
                    branch=branch,
                    summary=summary,
                    recorded_at=now_iso,
                )
            con.commit()
        finally:
            con.close()
        logger.info("Added episode for chat %d: %s", chat_id, summary[:80])
        return episode_id

    def _find_open_worklog_session(
        self,
        con: sqlite3.Connection,
        *,
        chat_id: int,
        message_thread_id: int | None,
        scope_key: str,
        provider: str | None,
        session_type: str | None,
        session_id: str | None,
    ) -> sqlite3.Row | None:
        con.row_factory = sqlite3.Row
        return con.execute(
            """
            SELECT *
            FROM worklog_sessions
            WHERE chat_id = ?
              AND scope_key = ?
              AND COALESCE(provider, '') = COALESCE(?, '')
              AND COALESCE(session_type, '') = COALESCE(?, '')
              AND (
                    COALESCE(session_id, '') = COALESCE(?, '')
                    OR (? IS NOT NULL AND session_id IS NULL)
                  )
              AND (
                    (? IS NULL AND message_thread_id IS NULL)
                    OR message_thread_id = ?
                  )
            ORDER BY
              CASE
                WHEN COALESCE(session_id, '') = COALESCE(?, '') THEN 0
                WHEN session_id IS NULL THEN 1
                ELSE 2
              END,
              CASE WHEN closed_at IS NULL THEN 0 ELSE 1 END,
              started_at DESC
            LIMIT 1
            """,
            (
                chat_id,
                scope_key,
                provider,
                session_type,
                session_id,
                session_id,
                message_thread_id,
                message_thread_id,
                session_id,
            ),
        ).fetchone()

    def _ensure_worklog_session(
        self,
        con: sqlite3.Connection,
        *,
        chat_id: int,
        message_thread_id: int | None,
        scope_key: str,
        provider: str | None,
        session_type: str | None,
        session_id: str | None,
        topic_label: str | None,
        topic_started_at: str | None,
        repo_path: str | None,
        branch: str | None,
        recorded_at: str,
    ) -> int:
        existing = self._find_open_worklog_session(
            con,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            scope_key=scope_key,
            provider=provider,
            session_type=session_type,
            session_id=session_id,
        )
        if existing is not None:
            con.execute(
                """
                UPDATE worklog_sessions
                SET session_id = COALESCE(session_id, ?),
                    topic_label = COALESCE(?, topic_label),
                    topic_started_at = COALESCE(?, topic_started_at),
                    repo_path = COALESCE(?, repo_path),
                    branch = COALESCE(?, branch),
                    last_seen_at = ?
                WHERE id = ?
                """,
                (
                    session_id,
                    topic_label,
                    topic_started_at,
                    repo_path,
                    branch,
                    recorded_at,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])

        cursor = con.execute(
            """
            INSERT INTO worklog_sessions (
                episode_id, chat_id, message_thread_id, scope_key, provider, session_type,
                session_id, topic_label, topic_started_at, repo_path, branch, summary,
                started_at, closed_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                chat_id,
                message_thread_id,
                scope_key,
                provider,
                session_type,
                session_id,
                topic_label,
                topic_started_at,
                repo_path,
                branch,
                None,
                recorded_at,
                None,
                recorded_at,
            ),
        )
        return int(cursor.lastrowid)

    def _record_summary_link(
        self,
        con: sqlite3.Connection,
        *,
        episode_id: int,
        chat_id: int,
        message_thread_id: int | None,
        scope_key: str | None,
        provider: str | None,
        session_type: str | None,
        session_id: str | None,
        topic_label: str | None,
        topic_started_at: str | None,
        repo_path: str | None,
        branch: str | None,
        summary: str,
        recorded_at: str,
    ) -> int:
        effective_scope = scope_key or f"{chat_id}:{message_thread_id if message_thread_id is not None else 'main'}"
        worklog_id = self._ensure_worklog_session(
            con,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            scope_key=effective_scope,
            provider=provider,
            session_type=session_type,
            session_id=session_id,
            topic_label=topic_label,
            topic_started_at=topic_started_at,
            repo_path=repo_path,
            branch=branch,
            recorded_at=recorded_at,
        )
        con.execute(
            """
            UPDATE worklog_sessions
            SET episode_id = ?,
                summary = ?,
                repo_path = COALESCE(?, repo_path),
                branch = COALESCE(?, branch),
                topic_label = COALESCE(?, topic_label),
                topic_started_at = COALESCE(?, topic_started_at),
                closed_at = COALESCE(closed_at, ?),
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                episode_id,
                summary,
                repo_path,
                branch,
                topic_label,
                topic_started_at,
                recorded_at,
                recorded_at,
                worklog_id,
            ),
        )
        return worklog_id

    def record_commit_link(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        scope_key: str | None,
        provider: str | None,
        session_type: str | None,
        session_id: str | None,
        repo_path: str,
        branch: str | None,
        commit_sha: str,
        short_sha: str | None,
        subject: str | None,
        authored_at: str | None,
        committed_at: str | None,
        files: list[dict[str, object]] | None = None,
        topic_label: str | None = None,
        topic_started_at: str | None = None,
    ) -> dict[str, object]:
        self._ensure_storage()
        if not commit_sha.strip():
            raise ValueError("commit_sha is required")
        recorded_at = committed_at or self._now_utc_iso()
        effective_scope = scope_key or f"{chat_id}:{message_thread_id if message_thread_id is not None else 'main'}"
        con = self._connect()
        try:
            worklog_id = self._ensure_worklog_session(
                con,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                scope_key=effective_scope,
                provider=provider,
                session_type=session_type,
                session_id=session_id,
                topic_label=topic_label,
                topic_started_at=topic_started_at,
                repo_path=repo_path,
                branch=branch,
                recorded_at=recorded_at,
            )
            cursor = con.execute(
                """
                INSERT INTO worklog_commits (
                    worklog_session_id, commit_sha, short_sha, subject, repo_path, branch, authored_at, committed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worklog_session_id, commit_sha) DO UPDATE SET
                    short_sha = excluded.short_sha,
                    subject = excluded.subject,
                    repo_path = excluded.repo_path,
                    branch = excluded.branch,
                    authored_at = excluded.authored_at,
                    committed_at = excluded.committed_at
                """,
                (
                    worklog_id,
                    commit_sha.strip(),
                    short_sha,
                    subject,
                    repo_path,
                    branch,
                    authored_at,
                    committed_at,
                ),
            )
            commit_row_id = int(cursor.lastrowid)
            if commit_row_id == 0:
                row = con.execute(
                    "SELECT id FROM worklog_commits WHERE worklog_session_id = ? AND commit_sha = ?",
                    (worklog_id, commit_sha.strip()),
                ).fetchone()
                commit_row_id = int(row[0]) if row else 0
            con.execute(
                """
                UPDATE worklog_sessions
                SET repo_path = COALESCE(?, repo_path),
                    branch = COALESCE(?, branch),
                    last_seen_at = ?
                WHERE id = ?
                """,
                (repo_path, branch, recorded_at, worklog_id),
            )
            if files:
                for file_entry in files:
                    path = str(file_entry.get("path", "")).strip()
                    if not path:
                        continue
                    con.execute(
                        """
                        INSERT INTO worklog_files (worklog_commit_id, path, additions, deletions)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(worklog_commit_id, path) DO UPDATE SET
                            additions = excluded.additions,
                            deletions = excluded.deletions
                        """,
                        (
                            commit_row_id,
                            path,
                            file_entry.get("additions"),
                            file_entry.get("deletions"),
                        ),
                    )
            con.commit()
        finally:
            con.close()
        return {
            "worklog_session_id": worklog_id,
            "commit_sha": commit_sha.strip(),
            "repo_path": repo_path,
            "branch": branch,
            "file_count": len(files or []),
        }

    def list_worklog_links(
        self,
        *,
        query: str | None = None,
        limit: int = 5,
        chat_id: int | None = None,
    ) -> list[dict[str, object]]:
        self._ensure_storage()
        keywords = self._extract_keywords(query or "")
        con = self._connect()
        con.row_factory = sqlite3.Row
        try:
            params: list[object] = []
            base_where = []
            if chat_id is not None:
                base_where.append("w.chat_id = ?")
                params.append(chat_id)
            where_sql = ""
            if keywords:
                search_expr = " OR ".join(keywords)
                where_parts = list(base_where)
                where_parts.append("episodes_fts MATCH ?")
                params.append(search_expr)
                where_sql = "WHERE " + " AND ".join(where_parts)
                rows = con.execute(
                    f"""
                    SELECT DISTINCT w.*, e.timestamp,
                           COALESCE(w.summary, e.summary) AS effective_summary
                    FROM worklog_sessions w
                    LEFT JOIN episodes e ON e.id = w.episode_id
                    LEFT JOIN episodes_fts f ON f.rowid = e.id
                    {where_sql}
                    ORDER BY COALESCE(w.closed_at, w.last_seen_at) DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            else:
                if base_where:
                    where_sql = "WHERE " + " AND ".join(base_where)
                rows = con.execute(
                    f"""
                    SELECT w.*, e.timestamp,
                           COALESCE(w.summary, e.summary) AS effective_summary
                    FROM worklog_sessions w
                    LEFT JOIN episodes e ON e.id = w.episode_id
                    {where_sql}
                    ORDER BY COALESCE(w.closed_at, w.last_seen_at) DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()

            results: list[dict[str, object]] = []
            for row in rows:
                commits = con.execute(
                    """
                    SELECT id, commit_sha, short_sha, subject, repo_path, branch, authored_at, committed_at
                    FROM worklog_commits
                    WHERE worklog_session_id = ?
                    ORDER BY COALESCE(committed_at, authored_at) DESC, id DESC
                    """,
                    (int(row["id"]),),
                ).fetchall()
                commit_items: list[dict[str, object]] = []
                files: list[dict[str, object]] = []
                for commit in commits:
                    commit_id = int(commit["id"])
                    commit_files = con.execute(
                        """
                        SELECT path, additions, deletions
                        FROM worklog_files
                        WHERE worklog_commit_id = ?
                        ORDER BY path ASC
                        """,
                        (commit_id,),
                    ).fetchall()
                    file_items = [
                        {
                            "path": file_row["path"],
                            "additions": file_row["additions"],
                            "deletions": file_row["deletions"],
                        }
                        for file_row in commit_files
                    ]
                    files.extend(file_items)
                    commit_items.append(
                        {
                            "commit_sha": commit["commit_sha"],
                            "short_sha": commit["short_sha"],
                            "subject": commit["subject"],
                            "repo_path": commit["repo_path"],
                            "branch": commit["branch"],
                            "authored_at": commit["authored_at"],
                            "committed_at": commit["committed_at"],
                            "files": file_items,
                        }
                    )
                results.append(
                    {
                        "worklog_session_id": int(row["id"]),
                        "episode_id": row["episode_id"],
                        "chat_id": row["chat_id"],
                        "message_thread_id": row["message_thread_id"],
                        "scope_key": row["scope_key"],
                        "provider": row["provider"],
                        "session_type": row["session_type"],
                        "session_id": row["session_id"],
                        "topic_label": row["topic_label"],
                        "topic_started_at": row["topic_started_at"],
                        "repo_path": row["repo_path"],
                        "branch": row["branch"],
                        "summary": row["effective_summary"],
                        "started_at": row["started_at"],
                        "closed_at": row["closed_at"],
                        "last_seen_at": row["last_seen_at"],
                        "timestamp": row["timestamp"],
                        "commits": commit_items,
                        "files": files,
                    }
                )
            return results
        finally:
            con.close()

    def search_episodes(self, query: str, limit: int = 5) -> list[dict]:
        """Search episodes via FTS5. Falls back to recent episodes if no query match."""
        self._ensure_storage()
        keywords = self._extract_keywords(query)

        con = self._connect()
        con.row_factory = sqlite3.Row
        try:
            rows: list[sqlite3.Row] = []

            if keywords:
                fts_query = " OR ".join(keywords)
                try:
                    rows = con.execute(
                        "SELECT e.* FROM episodes e "
                        "JOIN episodes_fts f ON e.id = f.rowid "
                        "WHERE episodes_fts MATCH ? "
                        "ORDER BY rank LIMIT ?",
                        (fts_query, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    # FTS query syntax error — fall back to recent
                    pass

            # Fallback: most recent episodes
            if not rows:
                rows = con.execute(
                    "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            return [dict(r) for r in rows]
        finally:
            con.close()

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract non-stop-word keywords from text for FTS5 search."""
        words = []
        for word in text.lower().split():
            # Strip punctuation
            cleaned = "".join(c for c in word if c.isalnum())
            if cleaned and cleaned not in _STOP_WORDS and len(cleaned) > 2:
                words.append(cleaned)
        return words[:10]  # Cap to prevent huge FTS queries

    # ── Display & management ─────────────────────────────────

    def format_for_display(self) -> str:
        """Human-readable memory dump for /memory command."""
        self._ensure_storage()
        parts: list[str] = []

        # Profile
        if self._profile_path.exists():
            content = self._profile_path.read_text().strip()
            parts.append(f"<b>user_profile.yaml</b>\n<pre>{content}</pre>")
        else:
            parts.append("<b>user_profile.yaml</b>\n<i>(not created yet)</i>")

        # Episodes
        con = self._connect()
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT timestamp, summary FROM episodes ORDER BY timestamp DESC LIMIT 10"
            ).fetchall()
        finally:
            con.close()

        if rows:
            lines = [f"- {r['timestamp'][:10]}: {r['summary']}" for r in rows]
            parts.append("<b>Episodes</b> (last 10)\n<pre>" + "\n".join(lines) + "</pre>")
        else:
            parts.append("<b>Episodes</b>\n<i>(none yet)</i>")

        return "\n\n".join(parts)

    def clear(self) -> None:
        """Reset all memory to defaults."""
        self._ensure_storage()
        self._profile_path.write_text(_PROFILE_TEMPLATE)
        con = self._connect()
        try:
            con.execute("DELETE FROM episodes")
            con.execute("DELETE FROM worklog_files")
            con.execute("DELETE FROM worklog_commits")
            con.execute("DELETE FROM worklog_sessions")
            # Rebuild FTS index
            con.execute("INSERT INTO episodes_fts(episodes_fts) VALUES ('rebuild')")
            con.commit()
        finally:
            con.close()
        logger.info("All memory cleared")
