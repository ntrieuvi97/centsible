#!/usr/bin/env python3
"""
Facts Engine for centsible.
Manages repository-specific micro-facts and conditional ephemeral injections.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.fact_helpers import (  # noqa: F401  re-exported: other modules and tests import these from here
    _CORRECTION_TRIGGER_RE,
    _QUESTION_START_RE,
    _build_recovery_pattern,
    _extract_correction_targets,
    _extract_invocation_signature,
    _facts_locked,
    COMMON_BINS,
    CONTEXT_SPAN,
    DEFAULT_MAX_PROMPT_FACTS,
    DEFAULT_SAVED_TOKENS_PER_HIT,
    DEFAULT_SAVED_TOKENS_PER_LOOP_STOP,
    DEFAULT_USD_PER_MTOK,
    GENERIC_TERMS,
    LINT_EXEMPT_CATEGORIES,
    MAX_FACT_CHARS,
    MIN_BARE_TERM_LEN,
    SAVED_WILDCARD_SPAN,
    STANDARD_TRIVIAL_COMMANDS,
    TOOL_CATEGORIES,
    WILDCARD_SPAN,
    atomic_write_text,
    bound_wildcards,
    check_fact_attribution,
    contextualize_pattern,
    debug_warn,
    derive_summary,
    detect_user_correction,
    estimate_tokens,
    extract_fact_prescriptions,
    file_lock,
    find_common_bin,
    fold_vietnamese,
    get_default_facts_path,
    get_global_facts_path,
    is_fact_missing_in_command,
    is_prescribed_command_present,
    lint_fact_content,
    lint_pattern,
    normalize_text,
    redact_secrets,
    regex_matches,
)


class FactsEngine:
    def __init__(self, storage_path: Optional[Path] = None, global_path: Optional[Path] = None):
        self.storage_path = storage_path or get_default_facts_path()
        # Optional second layer shared by all repos; a project fact with the same id/pattern wins.
        self.global_path = global_path if global_path != self.storage_path else None
        self.metrics_path = self.storage_path.parent / "metrics.json"

    def load_metrics(self) -> Dict[str, int]:
        """Load repository-level token and execution telemetry."""
        defaults = {
            "prompt_injections": 0,
            "fact_hits": 0,
            "attributed_hits": 0,
            "tool_hint_hits": 0,
            "injected_tokens": 0,
            "saved_discovery_tokens": 0,
            "loop_interventions": 0,
            "saved_loop_tokens": 0,
            "measured_loop_tokens": 0,
            "tool_calls_total": 0,
            "tool_calls_failed": 0,
            "first_try_successes": 0,
            "command_calls_total": 0,
            "command_calls_failed": 0,
            "first_try_cmd_successes": 0,
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
        except Exception as e:
            debug_warn("load_metrics", e)
            return defaults

    def update_metrics(self, **deltas: int) -> Dict[str, int]:
        """Atomically increment telemetry counters."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.metrics_path):
            metrics = self.load_metrics()
            for k, delta in deltas.items():
                if delta:
                    metrics[k] = metrics.get(k, 0) + int(delta)
            try:
                atomic_write_text(self.metrics_path, json.dumps(metrics, ensure_ascii=False, indent=2))
            except Exception as e:
                debug_warn("update_metrics", e)
        return metrics

    def get_metrics_summary(self, usd_per_mtok: float = DEFAULT_USD_PER_MTOK) -> Dict[str, Any]:
        """Compute ROI, token savings, and cents/USD saved."""
        m = self.load_metrics()
        facts = self.list_facts()
        # Reconcile with per-fact counters if facts were used before metrics.json existed
        fact_hits = max(m.get("fact_hits", 0), sum(f.get("hits", 0) for f in facts))
        attributed_hits = max(
            m.get("attributed_hits", 0),
            sum(f.get("attributed_hits", 0) for f in facts),
        )
        injected_tokens = max(
            m.get("injected_tokens", 0),
            sum(f.get("injected_tokens", f.get("hits", 0) * (estimate_tokens(f.get("fact", "")) + 4)) for f in facts),
        )
        saved_discovery = max(
            m.get("saved_discovery_tokens", 0),
            sum(f.get("saved_tokens", f.get("hits", 0) * DEFAULT_SAVED_TOKENS_PER_HIT) for f in facts),
        )
        auto_learned_count = max(
            m.get("auto_learned_facts", 0),
            sum(1 for f in facts if f.get("category") in ("auto-learned", "gotcha") and str(f.get("id", "")).startswith("auto_")),
        )
        base = {
            **m,
            "auto_learned_facts": auto_learned_count,
            "total_facts": len(facts),
            "fact_hits": fact_hits,
            "attributed_hits": attributed_hits,
            "injected_tokens": injected_tokens,
            "saved_discovery_tokens": saved_discovery,
        }
        return derive_summary(base, usd_per_mtok)

    @staticmethod
    def _read(path: Optional[Path]) -> List[Dict]:
        """Raw facts of one file; unreadable lines are skipped."""
        if not path or not path.exists():
            return []
        facts = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    facts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return facts

    def list_facts(self, category: Optional[str] = None, scope: Optional[str] = None) -> List[Dict]:
        """Facts from disk. scope='project'|'global' reads one file raw; default is the merged view
        (project + global, global ones tagged `_scope: global` and dropped when a project fact overrides them)."""
        if scope == "global":
            facts = self._read(self.global_path)
        elif scope == "project":
            facts = self._read(self.storage_path)
        else:
            facts = self._read(self.storage_path)
            ids = {f.get("id") for f in facts}
            pats = {f.get("pattern") for f in facts}
            facts += [
                dict(g, _scope="global") for g in self._read(self.global_path)
                if g.get("id") not in ids and g.get("pattern") not in pats
            ]
        return [f for f in facts if not category or f.get("category") == category]

    def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single fact by ID."""
        for f in self.list_facts():
            if f.get("id") == fact_id:
                return f
        return None

    @_facts_locked
    def update_fact_attribution(self, fact_id: str, scope: str = "project") -> bool:
        """Increment attributed_hits for a fact when its usage is confirmed."""
        path = self._scope_path(scope)
        facts = self._read(path)
        updated = False
        for f in facts:
            if f.get("id") == fact_id:
                f["attributed_hits"] = f.get("attributed_hits", 0) + 1
                f["last_attributed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                updated = True
                break
        if updated:
            self._write(path, facts)
            return True
        if scope == "project" and self.global_path:
            return self.update_fact_attribution(fact_id, scope="global")
        return False

    @_facts_locked
    def add_fact(
        self,
        pattern: str,
        fact: str,
        category: str = "general",
        fact_id: Optional[str] = None,
        est_savings: int = DEFAULT_SAVED_TOKENS_PER_HIT,
        scope: str = "project",
        force: bool = False,
    ) -> Dict:
        """Add or update a micro-fact in the project file, or the global one with scope='global'.

        Rejects patterns that would fire on unrelated prompts and facts too long to be worth injecting
        on every hit; `force=True` skips both checks.
        """
        path = self._scope_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        if len(pattern) > 500:
            raise ValueError("Regex pattern is too long (maximum 500 characters).")
        if not force and category not in LINT_EXEMPT_CATEGORIES:
            problem = lint_pattern(pattern)
            if problem:
                raise ValueError(
                    f"Pattern '{pattern}' is too loose: {problem}. Match the situation, not a keyword, e.g. "
                    "'pytest.{0,40}(fail|error)' or an identifier like 'deploy_staging' (--force to override)."
                )

        if pattern != "*":
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")
            if category != "always" and compiled.search(""):
                raise ValueError(
                    f"Pattern '{pattern}' matches empty text, so it would fire on every prompt "
                    "(use category 'always' for that)."
                )

        pattern = normalize_text(pattern)
        clean_fact = normalize_text(fact.strip().replace("\n", " "))
        if not clean_fact:
            raise ValueError("Fact content cannot be empty.")
        if not force and category not in LINT_EXEMPT_CATEGORIES:
            content_problem = lint_fact_content(clean_fact)
            if content_problem:
                raise ValueError(
                    f"Fact '{clean_fact}' is rejected: {content_problem} (--force to override)."
                )
        if len(clean_fact) > MAX_FACT_CHARS and not force:
            raise ValueError(
                f"Fact is {len(clean_fact)} chars (max {MAX_FACT_CHARS}); it is injected on every hit, "
                "so keep it to one imperative line (--force to override)."
            )
        pattern = bound_wildcards(pattern, SAVED_WILDCARD_SPAN)

        # Deterministic id: re-adding the same pattern+fact updates in place, a different fact on the
        # same pattern is kept alongside (builtin hash() is per-process random, time() collides within 1s).
        fact_id = fact_id or "fact_" + hashlib.sha1(f"{pattern}\0{clean_fact}".encode("utf-8")).hexdigest()[:10]

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        new_entry = {
            "id": fact_id,
            "pattern": pattern,
            "fact": clean_fact,
            "category": category,
            "hits": 0,
            "injected_tokens": 0,
            "saved_tokens": 0,
            "est_savings": est_savings,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        facts = self._read(path)
        updated = False
        for i, existing in enumerate(facts):
            if existing.get("id") == fact_id:
                new_entry["hits"] = existing.get("hits", 0)
                new_entry["injected_tokens"] = existing.get("injected_tokens", 0)
                new_entry["saved_tokens"] = existing.get("saved_tokens", 0)
                new_entry["created_at"] = existing.get("created_at", new_entry["created_at"])
                facts[i] = new_entry
                updated = True
                break

        if not updated:
            facts.append(new_entry)

        self._write(path, facts)
        return new_entry

    @_facts_locked
    def remove_fact(self, fact_id_or_pattern: str, scope: str = "project") -> bool:
        """Remove a fact by ID or pattern from the project file (or the global one with scope='global')."""
        path = self._scope_path(scope)
        facts = self._read(path)
        initial_len = len(facts)
        facts = [
            f for f in facts
            if f.get("id") != fact_id_or_pattern and f.get("pattern") != fact_id_or_pattern
        ]
        if len(facts) < initial_len:
            self._write(path, facts)
            return True
        return False

    @_facts_locked
    def prune_facts(
        self,
        min_hits: int = 20,
        max_attribution_rate: float = 0.0,
        max_age_days: int = 30,
        dry_run: bool = False,
        scope: str = "project",
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Prune low-utility or stale micro-facts to prevent long-term repository bloat.
        Returns (pruned_facts, kept_facts).
        """
        path = self._scope_path(scope)
        facts = self._read(path)
        pruned = []
        kept = []
        now = datetime.now(timezone.utc)

        for f in facts:
            hits = f.get("hits", 0)
            attr_hits = f.get("attributed_hits", 0)
            attr_rate = (attr_hits / hits) if hits > 0 else 0.0

            # Condition 1: High noise, zero utility (shown >= min_hits times, but rarely applied)
            is_zero_utility = (hits >= min_hits and attr_rate <= max_attribution_rate)

            # Condition 2: Stale with 0 hits after max_age_days
            created_str = f.get("created_at")
            is_stale = False
            if created_str and hits == 0:
                try:
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if (now - created_dt).days >= max_age_days:
                        is_stale = True
                except Exception:
                    pass

            if is_zero_utility or is_stale:
                reason = "zero utility" if is_zero_utility else f"stale (> {max_age_days}d with 0 hits)"
                f_copy = dict(f)
                f_copy["prune_reason"] = reason
                pruned.append(f_copy)
            else:
                kept.append(f)

        if not dry_run and pruned:
            self._write(path, kept)

        return pruned, kept

    @_facts_locked
    def match(
        self,
        text: str,
        record_hits: bool = True,
        max_facts: Optional[int] = None,
        exclude_categories: Optional[Set[str]] = None,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Match input text against patterns with optional capping, category filtering, and session deduplication."""
        if not text:
            return []

        excluded = set(exclude_ids or [])
        facts = self.list_facts()
        candidates = []

        for f in facts:
            if f.get("id") in excluded:
                continue
            pattern = f.get("pattern", "")
            if not pattern:
                continue
            cat = f.get("category", "general")
            if exclude_categories and cat in exclude_categories:
                continue
            is_always = (pattern == "*" or cat == "always")
            try:
                if is_always or regex_matches(pattern, text):
                    candidates.append(f)
            except re.error:
                continue

        # If capped, prioritize: 'always' facts first, then most specific (longer) patterns
        if max_facts and len(candidates) > max_facts:
            always = [f for f in candidates if f.get("category") == "always" or f.get("pattern") == "*"]
            others = [f for f in candidates if f not in always]
            others.sort(key=lambda x: len(x.get("pattern", "")), reverse=True)
            candidates = (always + others)[:max_facts]

        if not candidates:
            return []

        needs_save = False
        total_injected_delta = 0
        total_saved_delta = 0

        for f in candidates:
            if record_hits:
                tok_cost = estimate_tokens(f.get("fact", "")) + 4
                tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                f["hits"] = f.get("hits", 0) + 1
                f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                f["last_hit_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                total_injected_delta += tok_cost
                total_saved_delta += tok_saved
                needs_save = True

        if needs_save and record_hits:
            header_tokens = 12  # "[CENTSIBLE MICRO-SKILLS: Active Repository Facts]" header overhead
            self._save_merged(facts)
            self.update_metrics(
                prompt_injections=1,
                fact_hits=len(candidates),
                injected_tokens=total_injected_delta + header_tokens,
                saved_discovery_tokens=total_saved_delta,
            )

        return candidates

    @_facts_locked
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
        and agent runs 'bruno' or 'pytest' mid-turn without required flags, or edits a file
        governed by an architectural rule/gotcha).
        """
        args = tool_call.get("args") or {}
        cmd_text = (
            args.get("CommandLine")
            or args.get("command")
            or ""
        )
        path_text = (
            args.get("Path")
            or args.get("file_path")
            or args.get("path")
            or args.get("TargetFile")
            or args.get("SearchPath")
            or ""
        )
        target_text = f"{cmd_text} {path_text}".strip()
        if not target_text:
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
            category = f.get("category", "general")
            try:
                if regex_matches(pattern, target_text):
                    # For CLI commands with non-rule/gotcha facts, only fire if prescribed flags/subcommands are missing.
                    # For file/path tools (Read/Edit/Write) or explicit 'rule'/'gotcha' facts, fire once per session!
                    if cmd_text and category not in ("rule", "gotcha"):
                        if only_missing_flags and not is_fact_missing_in_command(f.get("fact", ""), cmd_text):
                            continue
                    elif cmd_text and only_missing_flags and is_prescribed_command_present(f.get("fact", ""), cmd_text):
                        continue
                    matched.append(f)
                    if record_hits:
                        tok_cost = estimate_tokens(f.get("fact", "")) + 4
                        tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                        f["hits"] = f.get("hits", 0) + 1
                        f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                        f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                        f["last_hit_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        injected_delta += tok_cost
                        saved_delta += tok_saved
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            self._save_merged(facts)
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

        # Same tool in both commands, even behind wrappers ('cd x && rtk proxy pytest', 'python3 -m pytest')
        target_bin = find_common_bin(failed_cmd)
        if not target_bin or find_common_bin(succeeded_cmd) != target_bin:
            return None

        # Ignore if only target file path or working directory changed (not a CLI invocation recovery)
        if _extract_invocation_signature(failed_cmd, target_bin) == _extract_invocation_signature(succeeded_cmd, target_bin):
            return None

        pattern = _build_recovery_pattern(target_bin, failed_cmd, succeeded_cmd)
        if not pattern:
            return None

        # Avoid overwriting manual facts for this pattern
        for existing in self.list_facts():
            if existing.get("pattern") == pattern and existing.get("category") != "auto-learned":
                return None

        fact_text = redact_secrets(
            f"Use '{succeeded_cmd.strip()}' instead of '{failed_cmd.strip()}' when running {target_bin}."
        )
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", target_bin)[:32]
        fact_id = f"auto_{slug}_{hashlib.sha1(pattern.encode('utf-8')).hexdigest()[:6]}"
        try:
            entry = self.add_fact(
                pattern=pattern,
                fact=fact_text,
                category="auto-learned",
                fact_id=fact_id,
            )
        except ValueError:  # rejects if pattern or fact doesn't pass strict zero-waste linting
            return None
        self.update_metrics(auto_learned_facts=1)
        return entry

    def auto_learn_from_correction(self, prompt: str) -> Optional[Dict]:
        """
        Automatically extract and store a domain/file 'gotcha' micro-fact when the user explicitly
        states a concise correction or rule with concrete technical targets (files, modules, CLI tools).
        """
        if not prompt or "?" in prompt:
            return None
        norm = normalize_text(prompt.strip().replace("\n", " "))
        # Only auto-persist concise single-rule directives (15..240 chars), never questions or long logs
        if len(norm) < 15 or len(norm) > 240 or _QUESTION_START_RE.search(norm):
            return None
        if not _CORRECTION_TRIGGER_RE.search(norm):
            return None

        targets = _extract_correction_targets(norm)
        if not targets:
            return None

        pattern = "|".join(targets[:2])
        if lint_pattern(pattern):  # bare tool names (pip, pytest) fire on every mention: anchor them in the correction's own words
            pattern = contextualize_pattern(targets[:2], norm)
            if not pattern:
                return None
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", targets[0])[:32]
        # Text digest keeps two different rules about the same file apart; the same prompt stays idempotent.
        fact_id = f"auto_gotcha_{slug}_{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:6]}"

        # Avoid overwriting manual facts with the same pattern
        for existing in self.list_facts():
            if existing.get("pattern") == pattern and not str(existing.get("id", "")).startswith("auto_"):
                return None

        try:
            entry = self.add_fact(
                pattern=pattern,
                fact=redact_secrets(norm),
                category="gotcha",
                fact_id=fact_id,
            )
        except ValueError:  # target too generic to anchor a pattern
            return None
        self.update_metrics(auto_learned_facts=1)
        return entry

    def format_ephemeral_message(
        self,
        matched_facts: List[Dict],
        correction_hint: Optional[str] = None,
        reflect_mode: Optional[str] = None,
    ) -> Optional[str]:
        """Format matched facts and optional correction nudge into an ultra-compact prompt injection block."""
        if not matched_facts and not correction_hint:
            return None

        lines: List[str] = []
        if matched_facts:
            lines.append("[CENTSIBLE MICRO-SKILLS: Active Repository Facts]")
            for f in matched_facts:
                lines.append(f"- {f.get('fact')}")
        if correction_hint:
            lines.append(correction_hint)

        return "\n".join(lines)

    def _scope_path(self, scope: str) -> Path:
        if scope == "global":
            if not self.global_path:
                raise ValueError("No global facts file configured for this engine.")
            return self.global_path
        return self.storage_path

    @staticmethod
    def _write(path: Path, facts: List[Dict]) -> None:
        # Hand-written `#` comments and unparsable lines are not facts, but must survive a rewrite.
        # shortcut: they move to the top of the file, original interleaving is not kept.
        kept: List[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    kept.append(s)
                    continue
                try:
                    json.loads(s)
                except json.JSONDecodeError:
                    kept.append(s)
        body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in facts)
        atomic_write_text(path, "".join(k + "\n" for k in kept) + body)

    def _save_merged(self, facts: List[Dict]) -> None:
        """Persist a merged view (callers hold both locks): project facts to the project file, global ones
        back into the global file by id, so global entries overridden by a project fact are left alone."""
        strip = lambda f: {k: v for k, v in f.items() if k != "_scope"}
        self._write(self.storage_path, [strip(f) for f in facts if f.get("_scope") != "global"])
        updated = {f.get("id"): strip(f) for f in facts if f.get("_scope") == "global"}
        if updated and self.global_path:
            self._write(self.global_path, [updated.get(g.get("id"), g) for g in self._read(self.global_path)])
