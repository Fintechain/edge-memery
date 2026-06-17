"""Repeatable local stress benchmark for the edge memory core."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from memory_server.curator import MemoryCurator
from memory_server.db import MemoryDB


class NullVectorStore:
    """Measure memory-core performance without embedding model variance."""

    def insert(self, *args, **kwargs) -> None:
        pass

    def insert_batch(self, *args, **kwargs) -> None:
        pass

    def update(self, *args, **kwargs) -> None:
        pass

    def delete(self, *args, **kwargs) -> None:
        pass

    def search(self, *args, **kwargs) -> list:
        return []


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * ratio))
    return ordered[index] * 1000


def result(name: str, operations: int, elapsed: float,
           latencies: list[float], errors: int = 0, **extra) -> dict:
    return {
        "case": name,
        "operations": operations,
        "elapsed_s": round(elapsed, 4),
        "ops_s": round(operations / elapsed, 1) if elapsed else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "errors": errors,
        **extra,
    }


def run(args: argparse.Namespace) -> list[dict]:
    temporary = tempfile.TemporaryDirectory() if not args.db else None
    db_path = Path(args.db) if args.db else Path(temporary.name) / "stress.db"
    db = MemoryDB(str(db_path))
    project = db.create_project(
        "stress-context", description="通用事务的高性能边端记忆。", context_type="general",
    )
    project_id = project["id"]
    curator = MemoryCurator(db, NullVectorStore())
    reports = []

    batch_latencies = []
    started = time.perf_counter()
    written = 0
    for offset in range(0, args.memories, args.batch_size):
        size = min(args.batch_size, args.memories - offset)
        rows = [{
            "project_id": project_id,
            "memory_type": "fact" if index % 4 else "goal",
            "title": f"memory-{index}",
            "content": f"事务记忆 {index}，用于压力测试检索、汇总和并发写入。",
            "importance": 0.6,
            "confidence": 0.9,
            "novelty": 0.5,
            "reusability": 0.7,
            "actionability": 0.6,
        } for index in range(offset, offset + size)]
        item_started = time.perf_counter()
        db.write_memories_batch(rows)
        batch_latencies.append(time.perf_counter() - item_started)
        written += size
    elapsed = time.perf_counter() - started
    reports.append(result(
        "batch_insert", written, elapsed, batch_latencies,
        batches=len(batch_latencies), batch_size=args.batch_size,
    ))

    curator.refresh_project_summary(project_id=project_id)
    refresh_latencies = []
    started = time.perf_counter()
    for _ in range(args.summary_iterations):
        item_started = time.perf_counter()
        curator.refresh_project_summary(project_id=project_id)
        refresh_latencies.append(time.perf_counter() - item_started)
    elapsed = time.perf_counter() - started
    reports.append(result(
        "summary_refresh", args.summary_iterations, elapsed, refresh_latencies,
        source_limit=120,
    ))

    read_latencies = []
    started = time.perf_counter()
    for _ in range(args.hot_reads):
        item_started = time.perf_counter()
        curator.get_top_level_context(project_id=project_id)
        read_latencies.append(time.perf_counter() - item_started)
    elapsed = time.perf_counter() - started
    reports.append(result("top_level_hot_read", args.hot_reads, elapsed, read_latencies))

    latency_lock = threading.Lock()
    mixed_latencies: list[float] = []
    errors = 0

    def mixed_operation(index: int) -> None:
        nonlocal errors
        item_started = time.perf_counter()
        try:
            selector = index % 10
            if selector < args.write_percent:
                db.write_memory(
                    project_id=project_id,
                    memory_type="event",
                    title=f"concurrent-{index}",
                    content=f"并发事务更新 {index}",
                    importance=0.5,
                    confidence=0.8,
                )
            elif selector < 8:
                db.search_memories(project_id=project_id, limit=20)
            else:
                curator.get_top_level_context(project_id=project_id)
        except Exception:
            with latency_lock:
                errors += 1
        finally:
            latency = time.perf_counter() - item_started
            with latency_lock:
                mixed_latencies.append(latency)

    order = list(range(args.operations))
    random.Random(42).shuffle(order)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(mixed_operation, index) for index in order]
        for future in as_completed(futures):
            future.result()
    elapsed = time.perf_counter() - started
    reports.append(result(
        "mixed_concurrent", args.operations, elapsed, mixed_latencies, errors,
        workers=args.workers, write_percent=args.write_percent * 10,
    ))

    final_count = db.get_stats(project_id)["total_memories"]
    reports.append({
        "case": "final_state",
        "database": str(db_path),
        "total_memories": final_count,
        "python_threads": args.workers,
    })

    if args.real_vector_items:
        from memory_server.backends.lancedb_backend import LanceDBStore

        vector_dir = Path(temporary.name if temporary else db_path.parent) / "vectors"
        store = LanceDBStore(str(vector_dir))
        vector_items = [{
            "id": f"vector-{index}",
            "project_id": project_id,
            "text": f"边端记忆 性能 架构 决策 任务 内容 {index}",
        } for index in range(args.real_vector_items)]
        started = time.perf_counter()
        store.insert_batch(vector_items)
        elapsed = time.perf_counter() - started
        reports.append(result(
            "real_vector_batch_insert", args.real_vector_items, elapsed, [elapsed],
        ))
        vector_latencies = []
        started = time.perf_counter()
        for index in range(args.vector_searches):
            item_started = time.perf_counter()
            store.search(f"架构任务 {index}", project_id=project_id, limit=10)
            vector_latencies.append(time.perf_counter() - item_started)
        elapsed = time.perf_counter() - started
        reports.append(result(
            "real_vector_search", args.vector_searches, elapsed, vector_latencies,
            vector_count=args.real_vector_items,
        ))
    db.close()
    if temporary:
        temporary.cleanup()
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Optional persistent benchmark database path")
    parser.add_argument("--memories", type=int, default=50000)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--operations", type=int, default=50000)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--write-percent", type=int, default=2,
                        help="Writes per ten mixed operations (0-10)")
    parser.add_argument("--summary-iterations", type=int, default=500)
    parser.add_argument("--hot-reads", type=int, default=20000)
    parser.add_argument("--real-vector-items", type=int, default=0)
    parser.add_argument("--vector-searches", type=int, default=500)
    args = parser.parse_args()
    if not 0 <= args.write_percent <= 10:
        parser.error("--write-percent must be between 0 and 10")
    for report in run(args):
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
