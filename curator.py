# -*- coding: utf-8 -*-
r"""Memory Curator v2 — integrates graphify + mempalace.

Pipeline:
  1. Ingest raw text → detect halls (mempalace) + extract knowledge (graphify)
  2. Score candidates → heuristic + structural scoring
  3. Deduplicate → semantic similarity against existing
  4. Store → SQLite (our db) + Palace vector store (mempalace) + Temporal KG (mempalace)
  5. Compact → merge similar, deprecate stale
  6. Prune → remove low-value memories

Confidence labels: EXTRACTED (from code) / INFERRED (deduced) / AMBIGUOUS (for review)
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from .config import get_config, Confidence
from .db import MemoryDB, _uid, _now

# ── Scoring ──────────────────────────────────────────────────────────────

SCORE_WEIGHTS = {
    "importance": 0.30,
    "confidence": 0.20,
    "novelty": 0.15,
    "reusability": 0.20,
    "actionability": 0.15,
}

WRITE_THRESHOLD = 0.75
CANDIDATE_THRESHOLD = 0.50
DEDUP_THRESHOLD = 0.85

LOW_VALUE_PATTERNS = [
    r"^.{,3}$",
    r"^(ok|好的|嗯|哦|对|是的|可以|行|好|明白了)[\s\!\.]*$",
    r"^test\s*$",
    r"^[a-z]{1,3}$",
    r"^[0-9\+\-\*/]+$",
]

PROTECTED_TYPES = {
    "decision", "api_contract", "db_schema", "project_summary",
    "project_core", "latest_conversation_summary",
}
TOP_LEVEL_MEMORY_TYPES = {
    "project_core", "latest_conversation_summary", "project_summary",
}

CORE_MEMORY_TYPES = {
    "architecture", "decision", "api_contract", "db_schema", "dependency",
    "integration", "product_rule", "concept", "goal", "principle", "preference",
}
IMPLEMENTATION_MEMORY_TYPES = {
    "frontend_page", "backend_endpoint", "api_contract", "db_schema", "bug",
    "deployment", "dependency", "integration", "task_snapshot", "event", "plan",
}
RULE_MEMORY_TYPES = {"coding_rule", "product_rule", "principle", "decision"}
COMPLETION_MARKERS = (
    "已完成", "完成了", "已实现", "已添加", "已修复", "已创建", "已迁移", "已部署",
    "completed", "implemented", "added", "fixed", "created", "migrated", "deployed",
)

CONTEXT_PROFILES = {
    "software": {
        "core": "代码与系统核心思想",
        "core_empty": "尚未记录核心架构或设计思想。",
        "completed": "当前实现与已完成内容",
        "pending": "进行中与待办",
        "constraints": "关键约束与技术决策",
    },
    "research": {
        "core": "研究核心问题、假设与方法",
        "core_empty": "尚未记录研究问题、假设或方法。",
        "completed": "研究进展与已完成工作",
        "pending": "待验证问题与下一步",
        "constraints": "关键证据、边界与决策",
    },
    "business": {
        "core": "业务目标、价值与核心策略",
        "core_empty": "尚未记录业务目标或核心策略。",
        "completed": "业务进展与已完成事项",
        "pending": "进行中事项与下一步",
        "constraints": "关键规则、风险与决策",
    },
    "learning": {
        "core": "学习目标与核心知识框架",
        "core_empty": "尚未记录学习目标或知识框架。",
        "completed": "已掌握与已完成内容",
        "pending": "待学习内容与下一步",
        "constraints": "学习原则、难点与决策",
    },
    "general": {
        "core": "事务核心目标、原则与脉络",
        "core_empty": "尚未记录核心目标、原则或背景。",
        "completed": "当前进展与已完成内容",
        "pending": "进行中事项与下一步",
        "constraints": "关键约束、偏好与决策",
    },
}


def is_low_value(text: str) -> bool:
    text = text.strip()
    if not text or len(text) < 5:
        return True
    for pattern in LOW_VALUE_PATTERNS:
        if re.match(pattern, text):
            return True
    return False


def compute_score(importance: float, confidence: float, novelty: float,
                  reusability: float, actionability: float) -> float:
    return round(
        importance * SCORE_WEIGHTS["importance"] +
        confidence * SCORE_WEIGHTS["confidence"] +
        novelty * SCORE_WEIGHTS["novelty"] +
        reusability * SCORE_WEIGHTS["reusability"] +
        actionability * SCORE_WEIGHTS["actionability"], 4)


def classify_score(score: float) -> str:
    if score >= WRITE_THRESHOLD:
        return "accept"
    elif score >= CANDIDATE_THRESHOLD:
        return "review"
    return "reject"


# ═══════════════════════════════════════════════════════════════════════════
# Memory Curator v2
# ═══════════════════════════════════════════════════════════════════════════

class MemoryCurator:
    def __init__(self, db: MemoryDB, vector_store) -> None:
        self.db = db
        self.vs = vector_store

        # Lazy imported backends
        self._palace = None
        self._pipeline = None
        self._context_cache: dict[str, tuple[int, dict]] = {}
        self._context_cache_lock = threading.RLock()
        self._summary_refresh_lock = threading.RLock()

    @property
    def palace(self):
        if self._palace is None:
            from . import palace as p
            self._palace = p
        return self._palace

    @property
    def pipeline(self):
        if self._pipeline is None:
            from . import pipeline as p
            self._pipeline = p
        return self._pipeline

    # ── Ingestion ─────────────────────────────────────────────────────────

    def ingest_conversation(
        self, project_name: str, conversation_text: str,
        source_type: str = "conversation",
    ) -> dict:
        """Ingest conversation, auto-accept strong memories, and refresh summary."""
        project = self.db.get_project_by_name(project_name)
        if not project:
            return {"error": f"Project '{project_name}' not found."}
        project_id = project["id"]

        # Ensure wing
        wing = self.palace.ensure_wing(self.db, project_name)
        wing_id = wing.get("id") if "id" in wing else None

        # Extract candidates
        candidates = self._extract_candidates(
            project_id, conversation_text, source_type, wing_id
        )

        accepted = []
        pending = []
        for candidate in candidates:
            if classify_score(candidate.get("score", 0)) == "accept":
                accepted.append(self.review_candidate(
                    candidate["id"], "accepted",
                    reason="Automatically accepted above write threshold.",
                    refresh_summary=False,
                ))
            else:
                pending.append(candidate)

        latest_conversation = self.update_latest_conversation_summary(
            project_id=project_id,
            conversation_text=conversation_text,
            source_type=source_type,
        )
        summary = self.refresh_project_summary(project_id=project_id)

        return {
            "project": project_name,
            "wing_id": wing_id,
            "source_type": source_type,
            "candidates_created": len(candidates),
            "candidates": candidates,
            "auto_accepted": len(accepted),
            "pending_review": len(pending),
            "accepted_results": accepted,
            "latest_conversation_summary": latest_conversation.get("summary"),
            "project_summary": summary.get("summary"),
        }

    def _extract_candidates(
        self, project_id: str, text: str, source_type: str, wing_id: str | None = None,
    ) -> list[dict]:
        """Extract candidates using heuristics + mempalace hall detection."""
        segments = re.split(r'(?<=[。！？\.\!\?\n])\s*', text)
        segments = [s.strip() for s in segments if s.strip()]

        candidates = []
        for seg in segments:
            if is_low_value(seg) or len(seg) < 10:
                continue

            scores = self._heuristic_score(seg)
            candidate_type = scores.pop("candidate_type", "fact")
            final_score = compute_score(**{k: v for k, v in scores.items()
                                          if k in ("importance", "confidence", "novelty",
                                                   "reusability", "actionability")})
            if classify_score(final_score) == "reject":
                continue

            # Route to hall using mempalace
            try:
                hall_id = self.palace.route_to_hall(seg)
            except Exception:
                hall_id = "general"

            candidate = self.db.create_candidate(
                project_id=project_id,
                source_type=source_type,
                raw_text=seg,
                extracted_title=self._extract_title(seg),
                extracted_content=seg,
                candidate_type=candidate_type,
                wing_id=wing_id,
                hall_id=hall_id,
                confidence_label=Confidence.INFERRED,
                reason=f"Heuristic+MemPalace. Hall={hall_id} Score={final_score:.3f}",
                importance=scores["importance"],
                confidence=scores["confidence"],
                novelty=scores["novelty"],
                reusability=scores["reusability"],
                actionability=scores["actionability"],
            )
            candidates.append(candidate)

        return candidates

    def _heuristic_score(self, text: str) -> dict:
        """Heuristic scoring for a text segment."""
        text_lower = text.lower()
        length = len(text)

        importance = min(0.95, 0.4 + length / 200)
        confidence = 0.55

        _imperative = ["必须", "不允许", "禁止", "严禁", "不能", "不要", "应当", "应该",
                       "must", "should", "always", "never", "require", "mandatory",
                       "规则", "rule", "规定", "约定", "规范"]
        _decision = ["决策", "决定", "确定", "确认", "选定", "选择", "采用",
                     "decided", "decision", "confirmed", "chose", "adopted"]
        _structural = ["架构", "architecture", "数据库", "database", "api", "接口",
                       "contract", "schema", "表结构", "拓扑", "模块"]
        _completion = [
            "已完成", "完成了", "已经完成", "已实现", "已经实现", "已修复", "已经修复",
            "completed", "implemented", "finished", "fixed", "done",
        ]
        _goal = ["目标", "希望", "想要", "为了", "期望", "goal", "objective", "want"]
        if any(kw in text_lower for kw in _imperative):
            confidence = min(0.95, confidence + 0.25)
        if any(kw in text_lower for kw in _decision):
            confidence = min(0.95, confidence + 0.15)
        if any(kw in text_lower for kw in _structural):
            confidence = min(0.95, confidence + 0.10)
        if any(kw in text_lower for kw in _completion):
            confidence = min(0.95, confidence + 0.10)
        if any(kw in text_lower for kw in _goal):
            confidence = min(0.95, confidence + 0.10)

        novelty = 0.55
        _change = ["新增", "修改", "重构", "改为", "替换", "迁移", "升级", "优化",
                   "refactor", "new", "migrate", "replace", "upgrade", "change"]
        _cleanup = ["删除", "移除", "清理", "废弃", "淘汰", "去掉", "不再",
                    "remove", "delete", "deprecate", "cleanup", "drop"]
        if any(kw in text_lower for kw in _change):
            novelty = min(0.95, novelty + 0.20)
        if any(kw in text_lower for kw in _cleanup):
            novelty = min(0.95, novelty + 0.15)
        if length > 80:
            novelty = min(0.95, novelty + 0.10)

        reusability = 0.50
        _pattern = ["规则", "rule", "pattern", "模式", "标准", "standard", "规范",
                    "最佳实践", "best practice", "惯例", "推荐"]
        _code = ["代码", "code", "实现", "implement", "配置", "config", "部署",
                 "deploy", "构建", "build", "编译", "compile"]
        if any(kw in text_lower for kw in _pattern):
            reusability = min(0.95, reusability + 0.25)
        if any(kw in text_lower for kw in _code):
            reusability = min(0.95, reusability + 0.20)
        if any(kw in text_lower for kw in _imperative):
            reusability = min(0.95, reusability + 0.10)

        actionability = 0.50
        _action = ["修复", "fix", "实现", "implement", "添加", "add", "修改", "change",
                   "删除", "remove", "创建", "create", "更新", "update"]
        _process = ["步骤", "step", "方法", "method", "流程", "process", "方案",
                    "策略", "strategy", "方案", "做法"]
        _forbidden = ["禁止", "不允许", "严禁", "不能", "不要", "never", "forbidden",
                      "不行", "不可", "不得"]
        if any(kw in text_lower for kw in _action):
            actionability = min(0.95, actionability + 0.25)
        if any(kw in text_lower for kw in _process):
            actionability = min(0.95, actionability + 0.15)
        if any(kw in text_lower for kw in _forbidden):
            actionability = min(0.95, actionability + 0.20)
        if any(kw in text_lower for kw in _completion):
            actionability = min(0.95, actionability + 0.15)
        if any(kw in text_lower for kw in _goal):
            actionability = min(0.95, actionability + 0.10)

        candidate_type = "fact"
        if any(kw in text_lower for kw in ["架构", "architecture", "模块", "module", "系统", "system design"]):
            candidate_type = "architecture"
        elif any(kw in text_lower for kw in ["api", "接口", "contract", "endpoint"]):
            candidate_type = "api_contract"
        elif any(kw in text_lower for kw in ["数据库", "database", "db", "schema", "sql"]):
            candidate_type = "db_schema"
        elif any(kw in text_lower for kw in ["bug", "错误", "error", "fix", "修复"]):
            candidate_type = "bug"
        elif any(kw in text_lower for kw in ["决策", "decision", "决定", "选择"]):
            candidate_type = "decision"
        elif any(kw in text_lower for kw in ["部署", "deploy", "release", "上线"]):
            candidate_type = "deployment"
        elif any(kw in text_lower for kw in ["任务", "task", "todo", "待办"]):
            candidate_type = "task_snapshot"
        elif any(kw in text_lower for kw in _imperative):
            candidate_type = "coding_rule" if any(
                kw in text_lower for kw in _code + _structural
            ) else "principle"
        elif any(kw in text_lower for kw in ["偏好", "喜欢", "倾向", "prefer", "preference"]):
            candidate_type = "preference"
        elif any(kw in text_lower for kw in ["计划", "下一步", "准备", "plan", "roadmap"]):
            candidate_type = "plan"
        elif any(kw in text_lower for kw in ["发生", "进行了", "会议", "event", "happened"]):
            candidate_type = "event"
        elif any(kw in text_lower for kw in _goal):
            candidate_type = "goal"

        return {
            "importance": importance, "confidence": confidence,
            "novelty": novelty, "reusability": reusability,
            "actionability": actionability, "candidate_type": candidate_type,
        }

    def _extract_title(self, text: str) -> str:
        first_sentence = re.split(r'[。！？\.\!\?\n]', text)[0].strip()
        if len(first_sentence) > 60:
            return first_sentence[:57] + "..."
        return first_sentence

    # ── Review ────────────────────────────────────────────────────────────

    def review_candidate(
        self, candidate_id: str, decision: str,
        merged_to: str | None = None, reason: str | None = None,
        refresh_summary: bool = True,
    ) -> dict:
        """Review a candidate memory."""
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            return {"error": f"Candidate '{candidate_id}' not found."}
        project_id = candidate["project_id"]

        review = self.db.create_review(candidate_id=candidate_id, decision=decision,
                                       merged_to=merged_to, reason=reason)

        if decision == "accepted":
            duplicates = self._find_duplicates(
                candidate.get("extracted_content") or candidate.get("raw_text", ""),
                project_id,
            )
            if duplicates:
                best_dup = duplicates[0]
                merged = self._merge_memories(best_dup["id"], candidate)
                self.db.update_candidate(candidate_id, status="merged",
                                         duplicate_of=best_dup["id"])
                summary = self.refresh_project_summary(project_id=project_id) if refresh_summary else None
                return {"decision": "merged", "merged_to": best_dup["id"],
                        "duplicate_similarity": best_dup.get("similarity", 0),
                        "merged_memory": merged, "review": review,
                        "project_summary": summary.get("summary") if summary else None}

            content = candidate.get("extracted_content") or candidate.get("raw_text", "")
            hall_id = candidate.get("hall_id", "general")
            wing_id = candidate.get("wing_id")

            memory = self.db.write_memory(
                project_id=project_id,
                memory_type=candidate.get("candidate_type", "coding_rule"),
                title=candidate.get("extracted_title", "") or self._extract_title(
                    candidate.get("raw_text", "")),
                content=content,
                wing_id=wing_id,
                hall_id=hall_id,
                confidence_label=candidate.get("confidence_label", Confidence.INFERRED),
                confidence_score=candidate.get("confidence", 0.5),
                importance=candidate.get("importance", 0),
                confidence=candidate.get("confidence", 0),
                novelty=candidate.get("novelty", 0),
                reusability=candidate.get("reusability", 0),
                actionability=candidate.get("actionability", 0),
            )
            self.db.update_candidate(candidate_id, status="accepted")
            vector_warning = None
            try:
                self.vs.insert(memory["id"], project_id, content)
            except Exception as exc:
                vector_warning = (
                    "Vector index write failed; memory was saved. "
                    f"{type(exc).__name__}: {exc}"
                )

            # The secondary MemPalace/Chroma embedding path is optional. It
            # loads ONNX Runtime and can be unstable on some Windows hosts.
            if get_config().palace_vector_enabled:
                try:
                    self.palace.store_in_palace(
                        memory["id"], content,
                        {"wing_id": wing_id or "", "hall_id": hall_id},
                    )
                except Exception:
                    pass

            summary = self.refresh_project_summary(project_id=project_id) if refresh_summary else None
            result = {"decision": "accepted", "memory": memory, "review": review,
                      "project_summary": summary.get("summary") if summary else None}
            if vector_warning:
                result["warning"] = vector_warning
            return result

        elif decision == "rejected":
            self.db.update_candidate(candidate_id, status="rejected")
            summary = self.refresh_project_summary(project_id=project_id) if refresh_summary else None
            return {"decision": "rejected", "review": review,
                    "project_summary": summary.get("summary") if summary else None}

        elif decision == "needs_review":
            self.db.update_candidate(candidate_id, status="needs_review")
            summary = self.refresh_project_summary(project_id=project_id) if refresh_summary else None
            return {"decision": "needs_review", "review": review,
                    "project_summary": summary.get("summary") if summary else None}

        elif decision == "merged":
            if merged_to:
                existing = self.db.get_memory(merged_to)
                if existing:
                    new_content = (existing["content"] + "\n\n[UPDATE] " +
                                   (candidate.get("extracted_content") or
                                    candidate.get("raw_text", "")))
                    self.db.update_memory(merged_to, content=new_content)
                    self.vs.update(merged_to, project_id, new_content)
                    self.db.update_candidate(candidate_id, status="merged",
                                             duplicate_of=merged_to)
                    summary = self.refresh_project_summary(project_id=project_id) if refresh_summary else None
                    return {"decision": "merged", "merged_to": merged_to, "review": review,
                            "project_summary": summary.get("summary") if summary else None}
            return {"error": "merged_to memory not found"}

        return {"decision": decision, "review": review}

    # ── Graph Pipeline ────────────────────────────────────────────────────

    def analyze_project_code(self, project_name: str, paths: list[str]) -> dict:
        """Run graphify pipeline on project code."""
        try:
            result = self.pipeline.run_pipeline(self.db, project_name, paths)
        except RuntimeError as exc:
            return {"error": str(exc)}
        if "error" in result:
            return result

        project = self.db.get_project_by_name(project_name)
        if not project:
            return result

        hubs = ", ".join(
            f"{node.get('label', node.get('id', 'unknown'))} ({node.get('degree', 0)})"
            for node in result.get("god_nodes", [])[:8]
        ) or "未检测到高连接节点"
        overview = (
            f"已扫描 {result.get('paths_scanned', 0)} 个路径，识别出 "
            f"{result.get('nodes_found', 0)} 个代码节点、{result.get('edges_found', 0)} 条关系、"
            f"{result.get('communities', 0)} 个模块社区。关键代码枢纽：{hubs}。"
        )
        existing = self.db.search_memories(
            project_id=project["id"], memory_type="architecture",
            keyword="[自动] 代码结构概览", limit=1,
        )
        if existing:
            code_memory = self.db.update_memory(existing[0]["id"], content=overview)
            try:
                self.vs.update(existing[0]["id"], project["id"], overview)
            except Exception as exc:
                result["warning"] = (
                    "Vector index update failed; memory was saved. "
                    f"{type(exc).__name__}: {exc}"
                )
        else:
            code_memory = self.db.write_memory(
                project_id=project["id"], memory_type="architecture",
                title="[自动] 代码结构概览", content=overview,
                confidence_label=Confidence.EXTRACTED, confidence_score=0.95,
                source_files=paths, importance=0.9, confidence=0.95,
                novelty=0.6, reusability=0.9, actionability=0.7,
            )
            try:
                self.vs.insert(code_memory["id"], project["id"], overview)
            except Exception as exc:
                result["warning"] = (
                    "Vector index write failed; memory was saved. "
                    f"{type(exc).__name__}: {exc}"
                )

        summary = self.refresh_project_summary(project_id=project["id"])
        result["code_overview_memory"] = code_memory
        result["project_summary"] = summary.get("summary")
        return result

    def extract_from_text(self, text: str) -> dict:
        """Extract structured knowledge from text using graphify."""
        try:
            return self.pipeline.extract_from_text(text)
        except RuntimeError as exc:
            return {"error": str(exc)}

    # ── Knowledge Graph ──────────────────────────────────────────────────

    def add_triple(self, subject: str, predicate: str, obj: str,
                   valid_from: str | None = None, valid_to: str | None = None,
                   confidence: float = 1.0, drawer_ref: str | None = None) -> dict:
        try:
            return self.palace.add_knowledge_triple(
                subject, predicate, obj, valid_from=valid_from, valid_to=valid_to,
                confidence=confidence, drawer_ref=drawer_ref)
        except RuntimeError as exc:
            return {"error": str(exc)}

    def query_entity(self, entity: str, as_of: str | None = None) -> dict:
        try:
            return self.palace.query_knowledge(entity, as_of=as_of)
        except RuntimeError as exc:
            return {"error": str(exc)}

    # ── Dedup / Merge / Compact / Prune ──────────────────────────────────

    def _find_duplicates(self, content: str, project_id: str) -> list[dict]:
        try:
            results = self.vs.search(content, project_id=project_id, limit=5)
        except Exception:
            return []
        duplicates = []
        for result in results:
            if result.similarity <= DEDUP_THRESHOLD:
                continue
            memory = self.db.get_memory(result.id)
            if not memory or memory.get("memory_type") in TOP_LEVEL_MEMORY_TYPES:
                continue
            duplicates.append({
                "id": result.id, "similarity": result.similarity, "text": result.text,
            })
        return duplicates

    def update_latest_conversation_summary(
        self,
        conversation_text: str,
        project_name: str | None = None,
        project_id: str | None = None,
        source_type: str = "conversation",
    ) -> dict:
        """Replace the pinned latest-conversation summary for a project."""
        project = (
            self.db.get_project(project_id) if project_id
            else self.db.get_project_by_name(project_name or "")
        )
        if not project:
            return {"error": f"Project '{project_name or project_id}' not found."}
        if not conversation_text or not conversation_text.strip():
            return {"error": "conversation_text cannot be empty."}

        content = self._build_latest_conversation_summary(
            conversation_text, source_type=source_type,
        )
        summary = self._upsert_top_level_memory(
            project_id=project["id"],
            memory_type="latest_conversation_summary",
            title=f"{project['name']} 最新对话总结",
            content=content,
            hall_id="events",
            tags=["pinned", "latest-conversation", "auto-generated"],
        )
        return {"status": "updated", "summary": summary}

    def refresh_project_summary(
        self, project_name: str | None = None, project_id: str | None = None,
        latest_conversation_text: str | None = None,
        source_type: str = "conversation",
    ) -> dict:
        """Serialize summary rebuilds so top-level singleton writes cannot race."""
        with self._summary_refresh_lock:
            return self._refresh_project_summary(
                project_name=project_name,
                project_id=project_id,
                latest_conversation_text=latest_conversation_text,
                source_type=source_type,
            )

    def _refresh_project_summary(
        self, project_name: str | None = None, project_id: str | None = None,
        latest_conversation_text: str | None = None,
        source_type: str = "conversation",
    ) -> dict:
        """Rebuild the project's durable, self-maintaining context summary."""
        project = (
            self.db.get_project(project_id) if project_id
            else self.db.get_project_by_name(project_name or "")
        )
        if not project:
            return {"error": f"Project '{project_name or project_id}' not found."}
        project_id = project["id"]

        if latest_conversation_text is not None:
            latest_result = self.update_latest_conversation_summary(
                project_id=project_id,
                conversation_text=latest_conversation_text,
                source_type=source_type,
            )
            if "error" in latest_result:
                return latest_result

        revision = self.db.get_project_revision(project_id)
        with self._context_cache_lock:
            cached = self._context_cache.get(project_id)
            if cached and cached[0] == revision:
                return cached[1]

        memories = self.db.list_summary_memories(project_id=project_id, limit=120)
        recent_memories = sorted(
            memories,
            key=lambda memory: (memory.get("updated_at", ""), memory.get("score", 0)),
            reverse=True,
        )
        decisions = self.db.list_decisions(project_id, limit=20)
        snapshots = self.db.list_task_snapshots(project_id, limit=100)

        latest_tasks = []
        seen_tasks = set()
        for snapshot in snapshots:
            task_key = snapshot.get("task_name", "").strip().lower()
            if task_key and task_key not in seen_tasks:
                seen_tasks.add(task_key)
                latest_tasks.append(snapshot)

        core = []
        completed = []
        pending = []
        constraints = []

        if project.get("description"):
            self._append_summary_item(core, project["description"], 8)

        for memory in memories:
            if memory.get("memory_type") in CORE_MEMORY_TYPES:
                self._append_summary_item(core, self._memory_summary_line(memory), 8)

        for decision in decisions:
            line = decision.get("title", "")
            if decision.get("content"):
                line += f": {self._compact_text(decision['content'])}"
            self._append_summary_item(core, line, 8)
            self._append_summary_item(constraints, line, 6)

        for task in latest_tasks:
            completed_items = self._parse_list(task.get("completed"))
            remaining_items = self._parse_list(task.get("remaining"))
            for item in completed_items:
                self._append_summary_item(completed, f"{task['task_name']}: {item}", 10)
            if task.get("status", "").lower() in {"completed", "done", "finished"}:
                self._append_summary_item(completed, f"{task['task_name']} ({task['status']})", 10)
            for item in remaining_items:
                self._append_summary_item(pending, f"{task['task_name']}: {item}", 8)
            if not remaining_items and task.get("status", "").lower() in {
                "pending", "in_progress", "in-progress", "active", "blocked",
            }:
                self._append_summary_item(
                    pending, f"{task['task_name']} ({task['status']})", 8,
                )

        for memory in recent_memories:
            text = f"{memory.get('title', '')} {memory.get('content', '')}".lower()
            is_implementation = memory.get("memory_type") in IMPLEMENTATION_MEMORY_TYPES
            has_completion_marker = any(marker in text for marker in COMPLETION_MARKERS)
            has_source = bool(self._parse_list(memory.get("source_files")))
            if is_implementation or has_completion_marker or has_source:
                self._append_summary_item(completed, self._memory_summary_line(memory), 10)
            if memory.get("memory_type") in RULE_MEMORY_TYPES:
                self._append_summary_item(constraints, self._memory_summary_line(memory), 6)

        pending_candidates = self.db.list_pending_candidates(project_id)
        if pending_candidates:
            self._append_summary_item(
                pending, f"有 {len(pending_candidates)} 条候选记忆等待审核", 8,
            )

        context_type = self._resolve_context_type(project, memories)
        profile = CONTEXT_PROFILES[context_type]

        core_content = "\n".join([
            f"# {profile['core']}：{project['name']}",
            "",
            self._format_summary_items(core, profile["core_empty"]),
        ])
        project_core = self._upsert_top_level_memory(
            project_id=project_id,
            memory_type="project_core",
            title=f"{project['name']} {profile['core']}",
            content=core_content,
            hall_id="architecture",
            tags=["pinned", "project-core", "auto-generated"],
        )
        latest_conversation = self.db.get_singleton_memory(
            project_id, "latest_conversation_summary",
        )

        content = "\n".join([
            f"# 持续上下文：{project['name']}",
            "",
            f"## {profile['core']}",
            self._format_summary_items(core, profile["core_empty"]),
            "",
            "## 最新对话总结",
            self._top_level_body(
                latest_conversation,
                "尚未记录最新对话。调用 ingest_conversation 后会自动更新。",
            ),
            "",
            f"## {profile['completed']}",
            self._format_summary_items(completed, "尚未记录已完成内容。"),
            "",
            f"## {profile['pending']}",
            self._format_summary_items(pending, "当前没有已记录的待办。"),
            "",
            f"## {profile['constraints']}",
            self._format_summary_items(constraints, "尚未记录关键约束。"),
        ])

        summary = self._upsert_top_level_memory(
            project_id=project_id,
            memory_type="project_summary",
            title=f"{project['name']} 自动项目摘要",
            content=content,
            hall_id="architecture",
            tags=["pinned", "auto-generated", "project-context"],
        )

        context = {
            "status": "refreshed",
            "project_core": project_core,
            "latest_conversation_summary": latest_conversation,
            "summary": summary,
        }
        with self._context_cache_lock:
            self._context_cache[project_id] = (revision, context)
        return context

    def _upsert_top_level_memory(
        self,
        project_id: str,
        memory_type: str,
        title: str,
        content: str,
        hall_id: str,
        tags: list[str],
    ) -> dict:
        existing = self.db.get_singleton_memory(project_id, memory_type)
        serialized_tags = json.dumps(tags, ensure_ascii=False)
        if existing:
            if (
                existing.get("title") == title
                and existing.get("content") == content
                and existing.get("tags") == serialized_tags
            ):
                return existing
            memory = self.db.update_memory(
                existing["id"], title=title, content=content,
                tags=serialized_tags,
                importance=1.0, confidence=0.98, novelty=0.5,
                reusability=1.0, actionability=0.95,
            )
            self._invalidate_context_cache(project_id)
            return memory or existing

        memory = self.db.write_memory(
            project_id=project_id,
            memory_type=memory_type,
            title=title,
            content=content,
            hall_id=hall_id,
            confidence_label=Confidence.INFERRED,
            confidence_score=0.98,
            tags=tags,
            importance=1.0,
            confidence=0.98,
            novelty=0.5,
            reusability=1.0,
            actionability=0.95,
        )
        self._invalidate_context_cache(project_id)
        return memory

    def get_top_level_context(
        self, project_name: str | None = None, project_id: str | None = None,
    ) -> dict:
        """Read pinned context without rebuilding it on every recall."""
        project = (
            self.db.get_project(project_id) if project_id
            else self.db.get_project_by_name(project_name or "")
        )
        if not project:
            return {"error": f"Project '{project_name or project_id}' not found."}
        project_id = project["id"]
        with self._context_cache_lock:
            cached = self._context_cache.get(project_id)
            if cached:
                return cached[1]
        top_level = self.db.get_top_level_memories(project_id)
        project_core = top_level.get("project_core")
        summary = top_level.get("project_summary")
        if not project_core or not summary:
            return self.refresh_project_summary(project_id=project_id)
        context = {
            "status": "ready",
            "project_core": project_core,
            "latest_conversation_summary": top_level.get("latest_conversation_summary"),
            "summary": summary,
        }
        with self._context_cache_lock:
            self._context_cache[project_id] = (
                self.db.get_project_revision(project_id), context,
            )
        return context

    def _invalidate_context_cache(self, project_id: str) -> None:
        with self._context_cache_lock:
            self._context_cache.pop(project_id, None)

    @staticmethod
    def _resolve_context_type(project: dict, memories: list[dict]) -> str:
        configured = (project.get("context_type") or "auto").lower()
        if configured in CONTEXT_PROFILES:
            return configured
        sample = " ".join([
            project.get("name", ""), project.get("description", ""),
            *[f"{m.get('memory_type', '')} {m.get('title', '')}" for m in memories[:40]],
        ]).lower()
        type_scores = {
            "software": sum(token in sample for token in (
                "代码", "软件", "api", "数据库", "架构", "architecture",
                "frontend", "backend", "deploy", "memory server",
            )),
            "research": sum(token in sample for token in (
                "研究", "论文", "实验", "假设", "证据", "research", "paper", "experiment",
            )),
            "business": sum(token in sample for token in (
                "业务", "客户", "市场", "营收", "销售", "business", "customer", "revenue",
            )),
            "learning": sum(token in sample for token in (
                "学习", "课程", "考试", "知识", "掌握", "study", "course", "learn",
            )),
        }
        best_type, best_score = max(type_scores.items(), key=lambda item: item[1])
        return best_type if best_score > 0 else "general"

    def _build_latest_conversation_summary(
        self, conversation_text: str, source_type: str,
    ) -> str:
        segments = re.split(r'(?<=[。！？.!?\n])\s*', conversation_text)
        points = []
        completed = []
        requirements = []
        requirement_markers = (
            "要求", "必须", "需要", "希望", "想要", "不要", "不能", "避免", "应该", "问题",
            "must", "need", "want", "should", "avoid", "problem", "todo", "next",
        )
        for segment in segments:
            compact = self._compact_text(segment, 220)
            if not compact or is_low_value(compact):
                continue
            self._append_summary_item(points, compact, 8)
            lowered = compact.lower()
            if any(marker in lowered for marker in COMPLETION_MARKERS):
                self._append_summary_item(completed, compact, 5)
            if any(marker in lowered for marker in requirement_markers):
                self._append_summary_item(requirements, compact, 6)

        if not points:
            points = [self._compact_text(conversation_text, 500)]

        return "\n".join([
            "# 最新对话总结",
            f"来源：{source_type}",
            "",
            "## 本次对话要点",
            self._format_summary_items(points, "本次对话没有可提取的有效内容。"),
            "",
            "## 明确完成",
            self._format_summary_items(completed, "本次对话未明确声明已完成内容。"),
            "",
            "## 要求与后续",
            self._format_summary_items(requirements, "本次对话未明确提出后续要求。"),
        ])

    @staticmethod
    def _top_level_body(memory: dict | None, empty_message: str) -> str:
        if not memory:
            return f"- {empty_message}"
        lines = memory.get("content", "").splitlines()
        body = [
            line for line in lines
            if not line.startswith("# ")
            and not line.startswith("更新时间：")
            and not line.startswith("来源：")
        ]
        compact = "\n".join(body).strip()
        return compact or f"- {empty_message}"

    @staticmethod
    def _parse_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _compact_text(text: str, limit: int = 180) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        return compact if len(compact) <= limit else compact[:limit - 3].rstrip() + "..."

    def _memory_summary_line(self, memory: dict) -> str:
        title = self._compact_text(memory.get("title", ""), 80)
        content = self._compact_text(memory.get("content", ""))
        if content.lower().startswith(title.lower()):
            return content
        return f"{title}: {content}" if content else title

    @staticmethod
    def _append_summary_item(items: list[str], item: str, limit: int) -> None:
        normalized = re.sub(r"\s+", " ", item or "").strip()
        if not normalized:
            return
        keys = {existing.lower() for existing in items}
        if normalized.lower() not in keys and len(items) < limit:
            items.append(normalized)

    @staticmethod
    def _format_summary_items(items: list[str], empty_message: str) -> str:
        return "\n".join(f"- {item}" for item in items) if items else f"- {empty_message}"

    def _merge_memories(self, existing_id: str, candidate: dict) -> dict:
        existing = self.db.get_memory(existing_id)
        if not existing:
            return {}
        new_content = (existing["content"] + "\n\n---\n[MERGED] " +
                       (candidate.get("extracted_content") or candidate.get("raw_text", "")))
        self.db.update_memory(existing_id, content=new_content)
        project_id = candidate.get("project_id", existing.get("project_id", ""))
        try:
            self.vs.update(existing_id, project_id, new_content)
        except Exception:
            pass
        return self.db.get_memory(existing_id) or {}

    def compact_project_memory(self, project_name: str) -> dict:
        """Compact project memories by merging low-score ones."""
        project = self.db.get_project_by_name(project_name)
        if not project:
            return {"error": f"Project '{project_name}' not found."}
        project_id = project["id"]

        memories = self.db.list_memories(project_id=project_id, limit=500)
        before = len(memories)

        merged = 0
        deprecated = 0
        removed = 0

        seen_by_type: dict[str, list] = {}
        for m in memories:
            mt = m.get("memory_type", "other")
            seen_by_type.setdefault(mt, []).append(m)

        for mt, group in seen_by_type.items():
            if mt in PROTECTED_TYPES:
                continue
            while len(group) > 5:
                lowest = min(group, key=lambda m: m.get("score", 0))
                if lowest.get("score", 0) < 0.3:
                    self.db.delete_memory(lowest["id"])
                    try:
                        self.vs.delete(lowest["id"])
                    except Exception:
                        pass
                    removed += 1
                    group.remove(lowest)
                else:
                    self.db.deprecate_memory(lowest["id"])
                    deprecated += 1
                    group.remove(lowest)

        after = len(self.db.list_memories(project_id=project_id, limit=500))
        summary = f"Compacted {project_name}: {before} → {after} memories"
        self.db.create_compaction(
            project_id=project_id, scope="all", memories_before=before,
            memories_after=after, removed_count=removed, merged_count=merged,
            deprecated_count=deprecated, summary=summary,
        )
        project_summary = self.refresh_project_summary(project_id=project_id)
        return {"memories_before": before, "memories_after": after,
                "removed": removed, "merged": merged, "deprecated": deprecated,
                "project_summary": project_summary.get("summary")}

    def prune_low_value_memories(self, project_name: str) -> dict:
        """Prune low-value memories."""
        project = self.db.get_project_by_name(project_name)
        if not project:
            return {"error": f"Project '{project_name}' not found."}
        project_id = project["id"]

        deleted = 0
        deprecated = 0
        memories = self.db.list_memories(project_id=project_id, limit=500)
        for m in memories:
            if m.get("memory_type") in PROTECTED_TYPES:
                continue
            if (
                m.get("score", 0) < 0.25 and
                m.get("hit_count", 0) == 0 and
                m.get("importance", 0) < 0.3
            ):
                if is_low_value(m.get("content", "")):
                    self.db.delete_memory(m["id"])
                    try:
                        self.vs.delete(m["id"])
                    except Exception:
                        pass
                    deleted += 1
                else:
                    self.db.deprecate_memory(m["id"])
                    deprecated += 1

        summary = self.refresh_project_summary(project_id=project_id)
        return {"deleted": deleted, "deprecated": deprecated,
                "project_summary": summary.get("summary")}
