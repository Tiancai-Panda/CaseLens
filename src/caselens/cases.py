"""Persistent case ownership, managed uploads and resumable customer submissions."""

import inspect
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from caselens.agent import Agent
from caselens.assets import AssetStore
from caselens.customer import CustomerService
from caselens.replay import customer_replay_clients
from caselens.schemas import Policy, VisualEvidence, new_id
from caselens.store import EvidenceStore


class CaseStore:
    def __init__(self, state_dir):
        self.root = (Path(state_dir) / "cases").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / "cases.sqlite3", timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS runs (seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, result TEXT NOT NULL)")

    def directory(self, case_id):
        if not re.fullmatch(r"case_[0-9a-f]{32}", case_id):
            raise ValueError("invalid case_id")
        directory = (self.root / case_id).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError("case path escapes state directory")
        return directory

    def get(self, case_id):
        self.directory(case_id)
        row = self.connection.execute("SELECT payload FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("case not found in this state directory")
        return json.loads(row[0])

    def _put(self, record):
        self.connection.execute("INSERT OR REPLACE INTO cases VALUES (?, ?)", (record["case_id"], json.dumps(record, ensure_ascii=False)))

    def _validated_uploads(self, paths):
        if len(paths) > 8:
            raise ValueError("at most eight uploaded images")
        if not paths:
            return []
        root = Path(os.path.commonpath([str(Path(path).resolve().parent) for path in paths]))
        source = AssetStore(root)
        uploads = []
        for path in paths:
            asset = source.register(Path(path))
            uploads.append((asset, source.data(asset.asset_id)))
        return uploads

    def _copy_uploads(self, record, uploads):
        directory = self.directory(record["case_id"])
        directory.mkdir(parents=True, exist_ok=True)
        assets = AssetStore(directory)
        active_hashes = {image["content_hash"] for image in record["images"] if image["active"]}
        for index, (source, data) in enumerate(uploads):
            if source.content_hash in active_hashes:
                continue
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[source.mime_type]
            target = (directory / f"s{record['submission']}-{index}-{source.content_hash[:16]}{suffix}").resolve()
            if not target.is_relative_to(directory):
                raise ValueError("upload path escapes case directory")
            target.write_bytes(data)
            asset = assets.register(target)
            record["images"].append({"asset_id": asset.asset_id, "managed_path": str(target), "uploaded_from": source.source_path,
                                     "content_hash": asset.content_hash, "active": True, "submission": record["submission"]})
            active_hashes.add(source.content_hash)

    def create(self, paths, *, user_text, goal, metadata, policy, mode):
        if not user_text.strip() and not paths:
            raise ValueError("provide user text or an image")
        if len(user_text) > 8000:
            raise ValueError("user text exceeds limit")
        uploads = self._validated_uploads(paths)
        record = {"case_id": new_id("case"), "mode": mode, "goal": goal or "核实售后诉求并给出处理建议", "metadata": metadata,
                  "policy": policy.model_dump(), "submission": 1, "status": "partial", "running": False, "claim": None, "claim_at": None,
                  "messages": [{"submission": 1, "text": user_text, "kind": "initial"}], "images": [],
                  "snapshot": {"evidence": [], "analysis_counts": {}}, "last_result": None}
        self._copy_uploads(record, uploads)
        with self.connection:
            self._put(record)
        return record

    def claim(self, case_id):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.get(case_id)
            if record.get("running"):
                raise ValueError("case is running; stop the other process before --recover")
            record["running"], record["claim"], record["claim_at"] = True, new_id("claim"), time.time()
            self._put(record)
            self.connection.commit()
            return record["claim"]
        except BaseException:
            self.connection.rollback()
            raise

    def release(self, case_id, claim=None):
        with self.connection:
            record = self.get(case_id)
            if claim is not None and record["claim"] != claim:
                raise ValueError("run claim changed")
            record["running"], record["claim"], record["claim_at"] = False, None, None
            self._put(record)

    def submit(self, case_id, paths, *, user_text, metadata, replace_asset_id=None, replace_text=False, claim=None):
        uploads = self._validated_uploads(paths)
        if len(user_text) > 8000:
            raise ValueError("user text exceeds limit")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.get(case_id)
            if record["running"] and record["claim"] != claim:
                raise ValueError("case is running")
            if record["submission"] >= 8:
                raise ValueError("case submission budget exhausted; contact human support")
            changed_metadata = any(record["metadata"].get(key) != value for key, value in metadata.items())
            active = [image for image in record["images"] if image["active"]]
            active_hashes = {image["content_hash"] for image in active}
            new_uploads = [(asset, data) for asset, data in uploads if asset.content_hash not in active_hashes]
            if not user_text.strip() and not changed_metadata and not new_uploads:
                raise ValueError("provide new text, image or metadata; use --retry for an interrupted/failed run")
            if replace_asset_id:
                old = next((image for image in active if image["asset_id"] == replace_asset_id), None)
                if old is None or len(uploads) != 1:
                    raise ValueError("replace one active asset with exactly one image")
                old["active"] = False
            if sum(image["active"] for image in record["images"]) + len({asset.content_hash for asset, _ in uploads if asset.content_hash not in {image['content_hash'] for image in record['images'] if image['active']}}) > 8:
                raise ValueError("at most eight active original images; replace an old image")
            record["submission"] += 1
            record["metadata"].update(metadata)
            if user_text.strip():
                record["messages"].append({"submission": record["submission"], "text": user_text, "kind": "correction" if replace_text else "supplement"})
            self._copy_uploads(record, uploads)
            valid_roots = {image["asset_id"] for image in record["images"] if image["active"]}
            record["snapshot"]["evidence"] = [evidence for evidence in record["snapshot"]["evidence"] if evidence["asset"]["root_asset_id"] in valid_roots]
            record["snapshot"]["analysis_counts"] = {root: count for root, count in record["snapshot"]["analysis_counts"].items() if root in valid_roots}
            record["status"] = "partial"
            # Corrections/replacements invalidate old summaries; ordinary supplements keep action history only.
            if replace_asset_id or replace_text:
                record["snapshot"]["memory"] = {}
            else:
                old = record["snapshot"].get("memory", {})
                record["snapshot"]["memory"] = {"actions": [*old.get("prior_actions", []), *old.get("actions", [])][-32:],
                                                "errors": list(dict.fromkeys([*old.get("prior_errors", []), *old.get("errors", []), *old.get("controller_errors", [])]))[-16:]}
            self._put(record)
            self.connection.commit()
            return record
        except BaseException:
            self.connection.rollback()
            raise

    def checkpoint(self, case_id, claim, service):
        with self.connection:
            record = self.get(case_id)
            if not record["running"] or record["claim"] != claim:
                raise ValueError("run claim changed")
            record["snapshot"] = {"evidence": [e.model_dump() for e in service.evidence.values()], "analysis_counts": service.analysis_counts,
                                  "memory": {**getattr(service, "memory", {}), "submission": record["submission"], "policy_version": service.policy.version,
                                             "skill_versions": {s["name"]: s.get("version") for s in getattr(service, "active_skills", [])}}}
            self._put(record)

    def finish(self, case_id, claim, result):
        with self.connection:
            record = self.get(case_id)
            if not record["running"] or record["claim"] != claim:
                raise ValueError("run claim changed")
            record["status"], record["last_result"] = result.status, result.model_dump(mode="json")
            record["running"], record["claim"], record["claim_at"] = False, None, None
            self._put(record)
            self.connection.execute("INSERT INTO runs (case_id, result) VALUES (?, ?)", (case_id, result.model_dump_json()))

    def history(self, case_id):
        self.get(case_id)
        return [json.loads(row[0]) for row in self.connection.execute("SELECT result FROM runs WHERE case_id=? ORDER BY seq", (case_id,)).fetchall()]

    def restore(self, record):
        directory = self.directory(record["case_id"])
        assets = AssetStore(directory)
        for image in record["images"]:
            if image["active"]:
                asset = assets.register(Path(image["managed_path"]))
                if asset.asset_id != image["asset_id"] or asset.content_hash != image["content_hash"]:
                    raise ValueError("managed image changed")
        evidence = [VisualEvidence.model_validate(e) for e in record["snapshot"]["evidence"]]
        for item in evidence:
            original = assets.get(item.asset.root_asset_id)
            if item.asset.source_path != original.source_path:
                raise ValueError("persisted evidence source mismatch")
            if item.asset.parent_asset_id and item.asset.derived_path:
                assets.assets[item.asset.asset_id] = item.asset
                assets.data(item.asset.asset_id)
        messages = record["messages"]
        last_correction = max((index for index, message in enumerate(messages) if message["kind"] == "correction"), default=0)
        user_text = "\n".join(f"用户第{message['submission']}次提交：{message['text']}" for message in messages[last_correction:])
        return assets, evidence, user_text

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


async def run_customer_case(paths, *, user_text, goal, metadata, settings, policy=None, replay=None, case_id=None,
                            replace_asset_id=None, replace_text=False, retry=False, recover=False, runtime=None,
                            event_sink=None):
    from caselens.service import Runtime
    mode = "replay" if replay else "live"
    resuming = case_id is not None
    with CaseStore(settings.state_dir) as store:
        if case_id:
            record = store.get(case_id)
            if record["mode"] != mode:
                raise ValueError("case mode cannot change between live and replay")
            if policy is not None and policy.model_dump() != record["policy"]:
                raise ValueError("case policy is pinned; create a new case to change policy")
            if recover and record["running"]:
                if not record.get("claim_at") or time.time() - record["claim_at"] < 900:
                    raise ValueError("case is actively running; recover is allowed only after 15 minutes")
                store.release(case_id)
            if retry and (paths or user_text.strip() or metadata or replace_asset_id or replace_text):
                raise ValueError("--retry cannot include a new submission")
            if retry and record["status"] not in {"failed", "partial"} and not recover:
                raise ValueError("retry only interrupted or failed cases")
        else:
            if retry or recover or replace_asset_id or replace_text:
                raise ValueError("resume options require an existing case")
            record = store.create(paths, user_text=user_text, goal=goal, metadata=metadata, policy=policy or Policy(), mode=mode)
            case_id = record["case_id"]
        claim = store.claim(case_id)
        service_holder = [None]
        try:
            if event_sink:
                emitted = event_sink({"type": "case_started", "case_id": case_id})
                if inspect.isawaitable(emitted):
                    await emitted
            if resuming and not retry:
                record = store.submit(case_id, paths, user_text=user_text, metadata=metadata, replace_asset_id=replace_asset_id, replace_text=replace_text, claim=claim)
            elif record["submission"] > 1 or retry:
                record = store.get(case_id)
            rules = Policy.model_validate(record["policy"])
            assets, prior_evidence, text = store.restore(record)
            async def execute(clients):
                with EvidenceStore(settings.state_dir / "evidence.sqlite3") as cache:
                    service = CustomerService(assets, clients[0], clients[1], cache, rules, record["metadata"], settings,
                                              user_text=text, prior_evidence=prior_evidence, analysis_counts=record["snapshot"]["analysis_counts"])
                    service_holder[0] = service
                    service.prior_memory = record["snapshot"].get("memory", {})
                    service.checkpoint = lambda: store.checkpoint(case_id, claim, service)
                    result = await Agent(service, clients[2], mode=mode, event_sink=event_sink).run(record["goal"])
                    service.checkpoint()
                    result.case_id, result.submission = case_id, record["submission"]
                    if result.supplement:
                        mode_arg = f" --replay {mode}" if mode == "replay" else " --config caselens.json"
                        state_arg = f' --state-dir "{settings.state_dir}"'
                        command = f"caselens resume {case_id} --image 新照片.jpg --text 补充说明{mode_arg}{state_arg}"
                        result.next_actions = [*result.next_actions, command]
                        result.user_message += "\n补交材料后继续同一工单：" + command
                    if result.status == "failed" and not result.user_message:
                        result.user_message = "模型接口调用失败，已有材料已保存。修正配置后可使用 resume --retry。"
                    store.finish(case_id, claim, result)
                    return result
            if replay:
                return await execute(customer_replay_clients(replay))
            if runtime:
                return await execute((runtime.vlm, runtime.jev, runtime.controller))
            async with Runtime(settings) as owned:
                return await execute((owned.vlm, owned.jev, owned.controller))
        except BaseException:
            # Checkpoints keep completed observations and budgets even on cancellation.
            try:
                if service_holder[0] is not None:
                    store.checkpoint(case_id, claim, service_holder[0])
            except Exception:
                pass
            store.release(case_id, claim)
            raise
