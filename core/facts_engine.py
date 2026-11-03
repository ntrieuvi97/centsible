#!/usr/bin/env python3
"""
Facts Engine for centsible.
Manages repository-specific micro-facts and conditional ephemeral injections.
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SAVED_TOKENS_PER_HIT = 1500
DEFAULT_SAVED_TOKENS_PER_LOOP_STOP = 2000
DEFAULT_USD_PER_MTOK = 3.0


def normalize_text(text: str) -> str:
    """Normalize Unicode to NFC (fixes macOS NFD Vietnamese input mismatches)."""
    return unicodedata.normalize("NFC", text) if text else ""


def fold_vietnamese(text: str) -> str:
    """Strip Vietnamese diacritics (including đ/Đ) for accent-insensitive fallback matching."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def regex_matches(pattern: str, text: str) -> bool:
    """Match regex against text with NFC normalization and Vietnamese accent-insensitive fallback."""
    norm_pat = normalize_text(pattern)
    norm_txt = normalize_text(text)
    if re.search(norm_pat, norm_txt, re.IGNORECASE):
        return True
    folded_pat = fold_vietnamese(norm_pat)
    folded_txt = fold_vietnamese(norm_txt)
    return bool(re.search(folded_pat, folded_txt, re.IGNORECASE))


def estimate_tokens(text: str) -> int:
    """Estimate LLM token count (~4 UTF-8 bytes per token; accurate for both English & Vietnamese)."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def is_fact_missing_in_command(fact_text: str, cmd_text: str) -> bool:
    """
    Check if a micro-fact prescribes specific CLI flags (e.g. --condense, -q)
    or ASCII CLI subcommands in quotes that are missing from cmd_text.
    """
    if not fact_text or not cmd_text:
        return False
    flags = re.findall(r"(?:^|\s)(--[a-zA-Z0-9_-]+|-[a-zA-Z0-9])\b", fact_text)
    flags = [fl.strip() for fl in flags if fl.strip()]
    if flags:
        return any(fl not in cmd_text for fl in flags)
    quoted = [
        q for q in re.findall(r"['\"`]([^'\"`]{2,40})['\"`]", fact_text)
        if q.isascii()
    ]
    if quoted:
        return any(q not in cmd_text for q in quoted)
    return False


def get_default_facts_path(workspace_paths: Optional[List[str]] = None) -> Path:
    """Determine the appropriate facts.jsonl path."""
    candidates: List[Path] = []
    if workspace_paths:
        for ws in workspace_paths:
            try:
                ws_path = Path(ws).resolve()
                if ws_path.is_dir():
                    candidates.append(ws_path)
            except Exception:
                continue

    try:
        cwd_path = Path.cwd().resolve()
        if cwd_path not in candidates:
            candidates.append(cwd_path)
    except Exception:
        pass

    # 1. Project-local directory (.agents/ or .claude/ or root facts.jsonl)
    for base in candidates:
        for folder in (".agents", ".claude"):
            local_dir = base / folder
            if local_dir.exists() and local_dir.is_dir():
                return local_dir / "facts.jsonl"
        root_facts = base / "facts.jsonl"
        if root_facts.exists():
            return root_facts

    # 2. Neutral global fallback in ~/.centsible/facts.jsonl
    env_home = os.environ.get("CENTSIBLE_HOME")
    global_dir = Path(env_home).expanduser().resolve() if env_home else (Path.home() / ".centsible")
    global_dir.mkdir(parents=True, exist_ok=True)
    neutral_facts = global_dir / "facts.jsonl"

    # Seamless one-time migration from legacy ~/.gemini/antigravity/facts.jsonl if present
    if not neutral_facts.exists() and not env_home:
        legacy_dir = Path.home() / ".gemini" / "antigravity"
        legacy_facts = legacy_dir / "facts.jsonl"
        if legacy_facts.exists():
            try:
                neutral_facts.write_bytes(legacy_facts.read_bytes())
                legacy_metrics = legacy_dir / "metrics.json"
                neutral_metrics = global_dir / "metrics.json"
                if legacy_metrics.exists() and not neutral_metrics.exists():
                    neutral_metrics.write_bytes(legacy_metrics.read_bytes())
            except Exception:
                pass

    return neutral_facts


class FactsEngine:
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or get_default_facts_path()
        self.metrics_path = self.storage_path.parent / "metrics.json"

    def _ensure_storage(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.touch()

    def load_metrics(self) -> Dict[str, int]:
        """Load repository-level token and execution telemetry."""
        defaults = {
            "prompt_injections": 0,
            "fact_hits": 0,
            "tool_hint_hits": 0,
            "injected_tokens": 0,
            "saved_discovery_tokens": 0,
            "loop_interventions": 0,
            "saved_loop_tokens": 0,
            "tool_calls_total": 0,
            "tool_calls_failed": 0,
            "first_try_successes": 0,
            "auto_learned_facts": 0,
        }
        if not self.metrics_path.exists():
            return defaults
        try:
            with open(self.metrics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in defaults.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            return defaults

    def update_metrics(self, **deltas: int) -> Dict[str, int]:
        """Atomically increment telemetry counters."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        metrics = self.load_metrics()
        for k, delta in deltas.items():
            if delta:
                metrics[k] = metrics.get(k, 0) + int(delta)
        temp_path = self.metrics_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(metrics, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.metrics_path)
        except Exception:
            pass
        return metrics

    def get_metrics_summary(self, usd_per_mtok: float = DEFAULT_USD_PER_MTOK) -> Dict[str, Any]:
        """Compute ROI, token savings, and cents/USD saved."""
        m = self.load_metrics()
        facts = self.list_facts()
        # Reconcile with per-fact counters if facts were used before metrics.json existed
        fact_hits = max(m["fact_hits"], sum(f.get("hits", 0) for f in facts))
        injected_tokens = max(
            m["injected_tokens"],
            sum(f.get("injected_tokens", f.get("hits", 0) * (estimate_tokens(f.get("fact", "")) + 4)) for f in facts),
        )
        saved_discovery = max(
            m["saved_discovery_tokens"],
            sum(f.get("saved_tokens", f.get("hits", 0) * DEFAULT_SAVED_TOKENS_PER_HIT) for f in facts),
        )
        saved_loop = m["saved_loop_tokens"]
        gross_saved = saved_discovery + saved_loop
        net_saved = gross_saved - injected_tokens
        saved_usd = (net_saved / 1_000_000.0) * usd_per_mtok
        saved_cents = saved_usd * 100.0
        total_calls = m["tool_calls_total"]
        first_try_rate = (m["first_try_successes"] / total_calls * 100.0) if total_calls > 0 else 100.0
        error_rate = (m["tool_calls_failed"] / total_calls * 100.0) if total_calls > 0 else 0.0
        roi = (gross_saved / injected_tokens) if injected_tokens > 0 else 0.0

        return {
            **m,
            "total_facts": len(facts),
            "fact_hits": fact_hits,
            "injected_tokens": injected_tokens,
            "saved_discovery_tokens": saved_discovery,
            "saved_loop_tokens": saved_loop,
            "gross_saved_tokens": gross_saved,
            "net_saved_tokens": net_saved,
            "saved_usd": round(saved_usd, 4),
            "saved_cents": round(saved_cents, 2),
            "first_try_success_rate": round(first_try_rate, 1),
            "tool_error_rate": round(error_rate, 1),
            "roi_multiplier": round(roi, 1),
            "usd_per_mtok": usd_per_mtok,
        }

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
        est_savings: int = DEFAULT_SAVED_TOKENS_PER_HIT,
    ) -> Dict:
        """Add or update a micro-fact."""
        self._ensure_storage()

        if len(pattern) > 500:
            raise ValueError("Regex pattern is too long (maximum 500 characters).")

        if pattern != "*":
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                compiled.search("")
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

        pattern = normalize_text(pattern)
        clean_fact = normalize_text(fact.strip().replace("\n", " "))
        if not clean_fact:
            raise ValueError("Fact content cannot be empty.")

        fact_id = fact_id or f"fact_{int(time.time())}_{abs(hash(pattern)) % 10000}"

        new_entry = {
            "id": fact_id,
            "pattern": pattern,
            "fact": clean_fact,
            "category": category,
            "hits": 0,
            "injected_tokens": 0,
            "saved_tokens": 0,
            "est_savings": est_savings,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        facts = self.list_facts()
        updated = False
        for i, existing in enumerate(facts):
            if existing.get("id") == fact_id or existing.get("pattern") == pattern:
                new_entry["hits"] = existing.get("hits", 0)
                new_entry["injected_tokens"] = existing.get("injected_tokens", 0)
                new_entry["saved_tokens"] = existing.get("saved_tokens", 0)
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
        total_injected_delta = 0
        total_saved_delta = 0

        for f in facts:
            pattern = f.get("pattern", "")
            if not pattern:
                continue
            is_always = (pattern == "*" or f.get("category") == "always")
            try:
                if is_always or regex_matches(pattern, text):
                    matched.append(f)
                    if record_hits:
                        tok_cost = estimate_tokens(f.get("fact", "")) + 4
                        tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                        f["hits"] = f.get("hits", 0) + 1
                        f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                        f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                        f["last_hit_at"] = datetime.utcnow().isoformat() + "Z"
                        total_injected_delta += tok_cost
                        total_saved_delta += tok_saved
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            header_tokens = 24  # "[CENTSIBLE MICRO-SKILLS...]" header overhead
            self._save_all(facts)
            self.update_metrics(
                prompt_injections=1,
                fact_hits=len(matched),
                injected_tokens=total_injected_delta + header_tokens,
                saved_discovery_tokens=total_saved_delta,
            )

        return matched

    def match_tool_call(
        self,
        tool_call: Dict[str, Any],
        exclude_ids: Optional[List[str]] = None,
        only_missing_flags: bool = True,
        record_hits: bool = True,
    ) -> List[Dict]:
        """
        Match a tool call's command/arguments against stored micro-facts.
        Catches facts that were not triggered by the user prompt (e.g. user said 'fix bug'
        and agent runs 'bruno' or 'pytest' mid-turn without required flags).
        """
        args = tool_call.get("args") or {}
        cmd_text = (
            args.get("CommandLine")
            or args.get("command")
            or args.get("Path")
            or args.get("file_path")
            or ""
        )
        if not cmd_text:
            return []

        excluded = set(exclude_ids or [])
        facts = self.list_facts()
        matched = []
        needs_save = False
        injected_delta = 0
        saved_delta = 0

        for f in facts:
            fid = f.get("id")
            if fid in excluded:
                continue
            pattern = f.get("pattern", "")
            if not pattern or pattern == "*":
                continue
            try:
                if regex_matches(pattern, cmd_text):
                    if only_missing_flags and not is_fact_missing_in_command(f.get("fact", ""), cmd_text):
                        continue
                    matched.append(f)
                    if record_hits:
                        tok_cost = estimate_tokens(f.get("fact", "")) + 4
                        tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                        f["hits"] = f.get("hits", 0) + 1
                        f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                        f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                        f["last_hit_at"] = datetime.utcnow().isoformat() + "Z"
                        injected_delta += tok_cost
                        saved_delta += tok_saved
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            self._save_all(facts)
            self.update_metrics(
                fact_hits=len(matched),
                tool_hint_hits=len(matched),
                injected_tokens=injected_delta,
                saved_discovery_tokens=saved_delta,
            )

        return matched

    def auto_learn_from_recovery(self, failed_cmd: str, succeeded_cmd: str) -> Optional[Dict]:
        """
        Automatically extract a micro-fact when a command fails and a subsequent command
        using the same CLI tool succeeds with different flags or runner prefix.
        """
        if not failed_cmd or not succeeded_cmd or failed_cmd.strip() == succeeded_cmd.strip():
            return None

        f_parts = failed_cmd.strip().split()
        s_parts = succeeded_cmd.strip().split()
        if not f_parts or not s_parts:
            return None

        # Case 1: Same base executable, added flags (e.g. 'bruno run uat' -> 'bruno run uat --condense')
        f_bin = Path(f_parts[0]).name
        s_bin = Path(s_parts[0]).name

        # Case 2: Wrapped runner (e.g. 'pytest tests/' -> 'python3 -m pytest tests/' or 'poetry run pytest tests/')
        common_bins = {"pytest", "bruno", "npm", "pnpm", "yarn", "cargo", "go", "docker", "uv", "poetry", "ruff", "mypy"}
        target_bin = None
        if f_bin == s_bin and f_bin in common_bins:
            target_bin = f_bin
        elif f_bin in common_bins and f_bin in s_parts:
            target_bin = f_bin

        if not target_bin:
            return None

        # Avoid overwriting manual facts for this binary
        for existing in self.list_facts():
            if existing.get("pattern") == target_bin and existing.get("category") != "auto-learned":
                return None

        fact_text = f"Use '{succeeded_cmd.strip()}' instead of '{failed_cmd.strip()}' when running {target_bin}."
        entry = self.add_fact(
            pattern=target_bin,
            fact=fact_text,
            category="auto-learned",
            fact_id=f"auto_{target_bin}",
        )
        self.update_metrics(auto_learned_facts=1)
        return entry

    def format_ephemeral_message(self, matched_facts: List[Dict]) -> Optional[str]:
        """Format matched facts into a single concise prompt injection block."""
        if not matched_facts:
            return None

        lines = [
            "[CENTSIBLE MICRO-SKILLS: Active Repository Facts]",
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
