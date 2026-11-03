#!/usr/bin/env python3
"""
Facts Engine for agy-token-economizer.
Manages repository-specific micro-facts and conditional ephemeral injections.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_default_facts_path(workspace_paths: Optional[List[str]] = None) -> Path:
    """Determine the appropriate facts.jsonl path."""
    # 1. Workspace .agents directory if workspace is provided
    if workspace_paths:
        for ws in workspace_paths:
            try:
                ws_path = Path(ws).resolve()
                if not ws_path.is_dir():
                    continue
                candidate = ws_path / ".agents" / "facts.jsonl"
                if candidate.parent.exists() and candidate.parent.is_dir():
                    return candidate
                candidate_root = ws_path / "facts.jsonl"
                if candidate_root.exists():
                    return candidate_root
            except Exception:
                continue

    # 2. Current working directory .agents
    cwd_agents = Path.cwd().resolve() / ".agents" / "facts.jsonl"
    if cwd_agents.parent.exists() and cwd_agents.parent.is_dir():
        return cwd_agents

    # 3. Global fallback in ~/.gemini/antigravity/facts.jsonl
    global_dir = Path.home() / ".gemini" / "antigravity"
    global_dir.mkdir(parents=True, exist_ok=True)
    return global_dir / "facts.jsonl"


class FactsEngine:
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or get_default_facts_path()

    def _ensure_storage(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.touch()

    def list_facts(self, category: Optional[str] = None) -> List[Dict]:
        """Read all facts from disk."""
        if not self.storage_path.exists():
            return []

        facts = []
        with open(self.storage_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    item = json.loads(line)
                    if category and item.get("category") != category:
                        continue
                    facts.append(item)
                except json.JSONDecodeError:
                    continue
        return facts

    def add_fact(
        self,
        pattern: str,
        fact: str,
        category: str = "general",
        fact_id: Optional[str] = None,
    ) -> Dict:
        """Add or update a micro-fact."""
        self._ensure_storage()

        if len(pattern) > 500:
            raise ValueError("Regex pattern is too long (maximum 500 characters).")

        # Validate regex pattern
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            # Safe probe test
            compiled.search("")
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

        clean_fact = fact.strip().replace("\n", " ")
        if not clean_fact:
            raise ValueError("Fact content cannot be empty.")

        fact_id = fact_id or f"fact_{int(time.time())}_{abs(hash(pattern)) % 10000}"

        new_entry = {
            "id": fact_id,
            "pattern": pattern,
            "fact": clean_fact,
            "category": category,
            "hits": 0,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        facts = self.list_facts()
        updated = False
        for i, existing in enumerate(facts):
            if existing.get("id") == fact_id or existing.get("pattern") == pattern:
                new_entry["hits"] = existing.get("hits", 0)
                new_entry["created_at"] = existing.get("created_at", new_entry["created_at"])
                facts[i] = new_entry
                updated = True
                break

        if not updated:
            facts.append(new_entry)

        self._save_all(facts)
        return new_entry

    def remove_fact(self, fact_id_or_pattern: str) -> bool:
        """Remove a fact by ID or pattern."""
        facts = self.list_facts()
        initial_len = len(facts)
        facts = [
            f for f in facts
            if f.get("id") != fact_id_or_pattern and f.get("pattern") != fact_id_or_pattern
        ]
        if len(facts) < initial_len:
            self._save_all(facts)
            return True
        return False

    def match(self, text: str, record_hits: bool = True) -> List[Dict]:
        """Match input text against all patterns. Returns matching facts."""
        if not text:
            return []

        facts = self.list_facts()
        matched = []
        needs_save = False

        for f in facts:
            pattern = f.get("pattern", "")
            if not pattern:
                continue
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    matched.append(f)
                    if record_hits:
                        f["hits"] = f.get("hits", 0) + 1
                        f["last_hit_at"] = datetime.utcnow().isoformat() + "Z"
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            self._save_all(facts)

        return matched

    def format_ephemeral_message(self, matched_facts: List[Dict]) -> Optional[str]:
        """Format matched facts into a single concise prompt injection block."""
        if not matched_facts:
            return None

        lines = [
            "[TOKEN-ECONOMIZER MICRO-SKILLS: Active Repository Facts]",
            "Apply the following verified facts for this turn to avoid repetitive search/execution:",
        ]
        for f in matched_facts:
            lines.append(f"- {f.get('fact')}")

        return "\n".join(lines)

    def _save_all(self, facts: List[Dict]) -> None:
        temp_path = self.storage_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            for item in facts:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        temp_path.replace(self.storage_path)
