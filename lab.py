"""A deterministic, fully local social-activity research simulator.

This module has no networking code and never contacts email or social platforms.
Every account, mailbox, video and interaction is synthetic and stored in SQLite.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_VERSION = "1.0"
STATUSES = {"queued", "running", "paused", "completed", "canceled", "failed"}
DEFAULT_CONFIG = {
    "name": "Thử nghiệm mới",
    "seed": 2026,
    "account_count": 12,
    "creator_count": 3,
    "videos_per_creator": 3,
    "views_per_account": 2,
    "like_probability": 0.35,
    "follow_probability": 0.2,
    "delay_ms": 35,
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(value)) if value else str(uuid.uuid4())
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("request_id phải là UUID hợp lệ") from exc


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Cấu hình phải là object JSON")
    allowed = set(DEFAULT_CONFIG) | {"seed"}
    unknown = set(raw) - allowed - {"request_id"}
    if unknown:
        raise ValueError("Trường không được hỗ trợ: " + ", ".join(sorted(unknown)))
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in raw.items() if k != "request_id"})
    name = cfg["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("Tên thử nghiệm phải có từ 1 đến 80 ký tự")
    cfg["name"] = name.strip()
    for key, low, high in (("seed", 0, 2**32 - 1), ("account_count", 1, 500),
                           ("creator_count", 1, 20), ("videos_per_creator", 1, 20),
                           ("views_per_account", 1, 100), ("delay_ms", 0, 1000)):
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, int) or not low <= val <= high:
            raise ValueError(f"{key} phải là số nguyên từ {low} đến {high}")
    for key in ("like_probability", "follow_probability"):
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val <= 1:
            raise ValueError(f"{key} phải nằm trong khoảng 0 đến 1")
        cfg[key] = float(val)
    # Bound data volume while keeping useful small-scale experiments possible.
    if cfg["account_count"] * cfg["views_per_account"] > 50_000:
        raise ValueError("Tổng số lượt xem mô phỏng tối đa là 50.000")
    return cfg


class SimulationLab:
    def __init__(self, data_dir: str | Path, clock=utcnow):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "simulation.sqlite3"
        self.clock = clock
        self._init_db()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    config TEXT NOT NULL, status TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, finished_at TEXT, model_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS creators(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, handle TEXT NOT NULL, display_name TEXT NOT NULL,
                    UNIQUE(run_id, ordinal), UNIQUE(run_id, handle)
                );
                CREATE TABLE IF NOT EXISTS videos(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    creator_id TEXT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, title TEXT NOT NULL, UNIQUE(run_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS actors(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, fake_email TEXT NOT NULL, fake_handle TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
                    UNIQUE(run_id, ordinal), UNIQUE(run_id, fake_email), UNIQUE(run_id, fake_handle)
                );
                CREATE TABLE IF NOT EXISTS local_mail(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
                    subject TEXT NOT NULL, body TEXT NOT NULL, code TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(actor_id)
                );
                CREATE TABLE IF NOT EXISTS views(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, watch_seconds INTEGER NOT NULL,
                    UNIQUE(actor_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS likes(
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    PRIMARY KEY(actor_id, video_id)
                );
                CREATE TABLE IF NOT EXISTS follows(
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
                    creator_id TEXT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                    PRIMARY KEY(actor_id, creator_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step INTEGER NOT NULL, kind TEXT NOT NULL, actor_id TEXT,
                    target_id TEXT, target_label TEXT NOT NULL, message TEXT NOT NULL, sim_time TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_id ON events(run_id, id);
            """)
            # A process restart pauses an interrupted run. Its per-account stage is durable,
            # so resuming continues from a checkpoint without duplicating interactions.
            db.execute("UPDATE runs SET status='paused' WHERE status='running'")

    def create_run(self, raw: dict[str, Any]) -> dict[str, Any]:
        request_id = _uuid(raw.get("request_id"))
        cfg = validate_config(raw)
        created_id = None
        with self._db() as db:
            found = db.execute("SELECT id,config FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if found:
                if json.loads(found["config"]) != cfg:
                    raise ValueError("request_id này đã được dùng cho cấu hình khác")
                created_id = found["id"]
            if created_id is None:
                run_id, now = str(uuid.uuid4()), self.clock()
                db.execute("INSERT INTO runs(id,request_id,name,config,status,created_at,model_version) VALUES(?,?,?,?,?,?,?)",
                           (run_id, request_id, cfg["name"], json.dumps(cfg, sort_keys=True), "running", now, MODEL_VERSION))
                rng = random.Random(cfg["seed"] ^ 0x51A1)
                creators = []
                for i in range(cfg["creator_count"]):
                    cid = str(uuid.uuid5(uuid.UUID(run_id), f"creator:{i}"))
                    handle = f"creator_{i + 1:03d}"
                    db.execute("INSERT INTO creators VALUES(?,?,?,?,?)", (cid, run_id, i, handle, f"Nhà sáng tạo mô phỏng {i + 1}"))
                    creators.append(cid)
                titles = ["Đường phố buổi sớm", "Một ngày nhiều màu", "Góc nhìn thử nghiệm", "Chuyển động chậm", "Khung hình xanh"]
                vindex = 0
                for ci, cid in enumerate(creators):
                    for vi in range(cfg["videos_per_creator"]):
                        vid = str(uuid.uuid5(uuid.UUID(run_id), f"video:{vindex}"))
                        title = f"{titles[rng.randrange(len(titles))]} · {ci + 1}.{vi + 1}"
                        db.execute("INSERT INTO videos VALUES(?,?,?,?,?)", (vid, run_id, cid, vindex, title))
                        vindex += 1
                for i in range(cfg["account_count"]):
                    aid = str(uuid.uuid5(uuid.UUID(run_id), f"actor:{i}"))
                    db.execute("INSERT INTO actors(id,run_id,ordinal,fake_email,fake_handle,stage,created_at) VALUES(?,?,?,?,?,'queued',?)",
                               (aid, run_id, i, f"tester_{i + 1:04d}@mail.test", f"tester_{i + 1:04d}", now))
                self._event(db, run_id, 0, "run_started", None, None, cfg["name"], "Bắt đầu thử nghiệm mô phỏng nội bộ")
                created_id = run_id
        return self.summary(created_id)

    def _event(self, db, run_id, step, kind, actor_id, target_id, label, message):
        db.execute("INSERT INTO events(run_id,step,kind,actor_id,target_id,target_label,message,sim_time) VALUES(?,?,?,?,?,?,?,?)",
                   (run_id, step, kind, actor_id, target_id, label, message, self.clock()))

    def tick(self, run_id: str | None = None) -> bool:
        """Advance one account lifecycle step transactionally; return whether work occurred."""
        with self._db() as db:
            if run_id:
                runs = db.execute("SELECT * FROM runs WHERE id=? AND status='running'", (run_id,)).fetchall()
            else:
                runs = db.execute("SELECT * FROM runs WHERE status='running' ORDER BY created_at,id").fetchall()
            if not runs:
                return False
            run = runs[0]
            actor = db.execute("SELECT * FROM actors WHERE run_id=? AND stage!='completed' ORDER BY ordinal LIMIT 1", (run["id"],)).fetchone()
            if actor is None:
                db.execute("UPDATE runs SET status='completed',finished_at=? WHERE id=?", (self.clock(), run["id"]))
                self._event(db, run["id"], run["cursor"], "run_completed", None, None, run["name"], "Hoàn tất thử nghiệm mô phỏng")
                return True
            cfg = json.loads(run["config"])
            step = run["cursor"] + 1
            aid = actor["id"]
            if actor["stage"] == "queued":
                code = f"LAB-{random.Random(cfg['seed'] + actor['ordinal']).randrange(100000, 1000000)}"
                db.execute("INSERT OR IGNORE INTO local_mail(run_id,actor_id,subject,body,code,created_at) VALUES(?,?,?,?,?,?)",
                           (run["id"], aid, "[GOOGLE GIẢ LẬP] Thư xác minh nội bộ", f"Mã giả lập: {code}. Hộp thư Google này chỉ tồn tại trong cơ sở dữ liệu thử nghiệm nội bộ.", code, self.clock()))
                db.execute("UPDATE actors SET stage='email_created' WHERE id=?", (aid,))
                self._event(db, run["id"], step, "email_created", aid, None, actor["fake_email"], "Tạo hộp thư Google giả lập .test")
            elif actor["stage"] == "email_created":
                db.execute("UPDATE actors SET stage='email_verified' WHERE id=?", (aid,))
                self._event(db, run["id"], step, "email_verified", aid, None, actor["fake_email"], "Xác minh email Google giả lập trong hệ thống nội bộ")
            elif actor["stage"] == "email_verified":
                db.execute("UPDATE actors SET stage='social_created' WHERE id=?", (aid,))
                self._event(db, run["id"], step, "social_created", aid, None, actor["fake_handle"], "Tạo hồ sơ TikTok giả lập trong phòng thử nghiệm")
            elif actor["stage"] == "social_created":
                videos = db.execute("SELECT v.id,v.title,v.creator_id,c.handle FROM videos v JOIN creators c ON c.id=v.creator_id WHERE v.run_id=? ORDER BY v.ordinal", (run["id"],)).fetchall()
                creators = db.execute("SELECT id,handle FROM creators WHERE run_id=? ORDER BY ordinal", (run["id"],)).fetchall()
                rng = random.Random(cfg["seed"] * 1_000_003 + actor["ordinal"] * 97 + 11)
                view_count = cfg["views_per_account"]
                chosen = [rng.choice(videos) for _ in range(view_count)]
                for vi, video in enumerate(chosen):
                    seconds = rng.randint(2, 45)
                    db.execute("INSERT OR IGNORE INTO views(run_id,actor_id,video_id,ordinal,watch_seconds) VALUES(?,?,?,?,?)", (run["id"], aid, video["id"], vi, seconds))
                    self._event(db, run["id"], step, "view", aid, video["id"], video["title"], f"Xem mô phỏng {seconds} giây")
                    if rng.random() < cfg["like_probability"]:
                        db.execute("INSERT OR IGNORE INTO likes(run_id,actor_id,video_id) VALUES(?,?,?)", (run["id"], aid, video["id"]))
                        self._event(db, run["id"], step, "like", aid, video["id"], video["title"], "Thích mô phỏng")
                for creator in creators:
                    if rng.random() < cfg["follow_probability"]:
                        db.execute("INSERT OR IGNORE INTO follows(run_id,actor_id,creator_id) VALUES(?,?,?)", (run["id"], aid, creator["id"]))
                        self._event(db, run["id"], step, "follow", aid, creator["id"], creator["handle"], "Theo dõi mô phỏng")
                db.execute("UPDATE actors SET stage='completed' WHERE id=?", (aid,))
            db.execute("UPDATE runs SET cursor=? WHERE id=?", (step, run["id"]))
        return True

    def pause_run(self, run_id: str):
        return self._set_status(run_id, "paused", {"running"})

    def resume_run(self, run_id: str):
        return self._set_status(run_id, "running", {"paused", "queued"})

    def cancel_run(self, run_id: str):
        return self._set_status(run_id, "canceled", {"queued", "running", "paused"})

    def _set_status(self, run_id, status, allowed):
        with self._db() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Không tìm thấy thử nghiệm")
            if row["status"] in allowed:
                fin = self.clock() if status in {"completed", "canceled", "failed"} else None
                db.execute("UPDATE runs SET status=?,finished_at=COALESCE(?,finished_at) WHERE id=?", (status, fin, run_id))
        return self.summary(run_id)

    def repeat_run(self, run_id: str, request_id: str):
        with self._db() as db:
            row = db.execute("SELECT config FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Không tìm thấy thử nghiệm")
            cfg = json.loads(row["config"])
        cfg["name"] = cfg["name"] + " · lặp lại"
        cfg["request_id"] = request_id
        return self.create_run(cfg)

    def _metrics(self, db, run_id):
        actors = db.execute("SELECT SUM(stage!='queued') n, SUM(stage IN ('email_verified','social_created','completed')) verified, SUM(stage IN ('social_created','completed')) social FROM actors WHERE run_id=?", (run_id,)).fetchone()
        return {
            "emails": actors["n"] or 0, "verified_emails": actors["verified"] or 0,
            "social_accounts": actors["social"] or 0,
            "views": db.execute("SELECT COUNT(*) FROM views WHERE run_id=?", (run_id,)).fetchone()[0],
            "likes": db.execute("SELECT COUNT(*) FROM likes WHERE run_id=?", (run_id,)).fetchone()[0],
            "follows": db.execute("SELECT COUNT(*) FROM follows WHERE run_id=?", (run_id,)).fetchone()[0],
            "watch_seconds": db.execute("SELECT COALESCE(SUM(watch_seconds),0) FROM views WHERE run_id=?", (run_id,)).fetchone()[0],
        }

    def _summary(self, db, row):
        total = db.execute("SELECT COUNT(*) FROM actors WHERE run_id=?", (row["id"],)).fetchone()[0]
        done = db.execute("SELECT COUNT(*) FROM actors WHERE run_id=? AND stage='completed'", (row["id"],)).fetchone()[0]
        stage_row = db.execute("SELECT stage FROM actors WHERE run_id=? AND stage!='completed' ORDER BY ordinal LIMIT 1", (row["id"],)).fetchone()
        stage = {"queued": "email", "email_created": "verification", "email_verified": "social", "social_created": "interactions"}.get(stage_row[0] if stage_row else None, "completed")
        return {"id": row["id"], "name": row["name"], "status": row["status"], "stage": stage,
                "seed": json.loads(row["config"])["seed"], "config": json.loads(row["config"]),
                "created_at": row["created_at"], "finished_at": row["finished_at"],
                "progress": {"done": done, "total": total, "percent": round(done * 100 / total) if total else 0},
                "metrics": self._metrics(db, row["id"])}

    def summary(self, run_id: str):
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Không tìm thấy thử nghiệm")
            return self._summary(db, row)

    def public_state(self, run_id: str | None = None):
        with self._db() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC,id DESC LIMIT 100").fetchall()
            runs = [self._summary(db, r) for r in rows]
            selected = next((r for r in rows if r["id"] == run_id), None)
            if selected is None and rows:
                selected = next((r for r in rows if r["status"] == "running"), rows[0])
            payload = {"simulation_only": True, "model_version": MODEL_VERSION, "server_time": self.clock(),
                       "runs": runs, "selected": None, "totals": {
                           "runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                           "completed_runs": db.execute("SELECT COUNT(*) FROM runs WHERE status='completed'").fetchone()[0],
                           "emails": db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
                           "social_accounts": db.execute("SELECT COUNT(*) FROM actors WHERE stage IN ('social_created','completed')").fetchone()[0],
                           "views": db.execute("SELECT COUNT(*) FROM views").fetchone()[0],
                           "likes": db.execute("SELECT COUNT(*) FROM likes").fetchone()[0],
                           "follows": db.execute("SELECT COUNT(*) FROM follows").fetchone()[0]}}
            if selected:
                payload["selected"] = self._detail(db, selected)
            return payload

    def _detail(self, db, row):
        run_id = row["id"]
        summary = self._summary(db, row)
        videos = [dict(v) for v in db.execute("""SELECT v.id,v.title,v.creator_id,c.handle creator_handle,
                    (SELECT COUNT(*) FROM views x WHERE x.video_id=v.id) views,
                    (SELECT COUNT(*) FROM likes x WHERE x.video_id=v.id) likes
                    FROM videos v JOIN creators c ON c.id=v.creator_id WHERE v.run_id=? ORDER BY v.ordinal""", (run_id,))]
        creators = [dict(c) for c in db.execute("""SELECT c.id,c.handle,
                    (SELECT COUNT(*) FROM follows f WHERE f.creator_id=c.id) followers
                    FROM creators c WHERE c.run_id=? ORDER BY c.ordinal""", (run_id,))]
        events = [dict(e) for e in db.execute("""SELECT e.id,e.step,e.kind,a.fake_handle actor_handle,e.target_label,e.sim_time,e.message
                    FROM events e LEFT JOIN actors a ON a.id=e.actor_id WHERE e.run_id=? ORDER BY e.id DESC LIMIT 30""", (run_id,))]
        maxstep = row["cursor"]
        groups = db.execute("SELECT step,SUM(kind='view') views,SUM(kind='like') likes,SUM(kind='follow') follows FROM events WHERE run_id=? GROUP BY step ORDER BY step", (run_id,)).fetchall()
        start_step = max(0, maxstep - 24)
        totals = {"views": 0, "likes": 0, "follows": 0}
        for group in groups:
            if group["step"] <= start_step:
                for key in totals:
                    totals[key] += group[key] or 0
        timeline = [{"step": start_step, **totals}]
        grouped = {g["step"]: g for g in groups}
        for step in range(start_step + 1, maxstep + 1):
            g = grouped.get(step)
            if g:
                for key in totals:
                    totals[key] += g[key] or 0
            timeline.append({"step": step, **totals})
        summary.update({"videos": videos, "creators": creators, "recent_events": events,
                        "timeline": timeline, "fingerprint": self._fingerprint(db, run_id) if summary["status"] == "completed" else None})
        return summary

    def next_delay_ms(self):
        with self._db() as db:
            row = db.execute("SELECT config FROM runs WHERE status='running' ORDER BY created_at,id LIMIT 1").fetchone()
            return json.loads(row[0])["delay_ms"] if row else None

    def _fingerprint(self, db, run_id):
        config = json.loads(db.execute("SELECT config FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        config.pop("name", None)
        items = [json.dumps(config, sort_keys=True)]
        for table, columns in (("videos", "ordinal,title"), ("views", "actor_id,video_id,ordinal,watch_seconds"), ("likes", "actor_id,video_id"), ("follows", "actor_id,creator_id")):
            # Exclude per-run UUIDs and represent actors/targets by stable ordinals.
            if table == "videos":
                vals = db.execute("SELECT ordinal,title FROM videos WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
                items.extend("v:" + json.dumps(list(x)) for x in vals)
            elif table == "views":
                vals = db.execute("SELECT a.ordinal,v.ordinal,x.ordinal,x.watch_seconds FROM views x JOIN actors a ON a.id=x.actor_id JOIN videos v ON v.id=x.video_id WHERE x.run_id=? ORDER BY a.ordinal,x.ordinal", (run_id,)).fetchall()
                items.extend("w:" + json.dumps(list(x)) for x in vals)
            elif table == "likes":
                vals = db.execute("SELECT a.ordinal,v.ordinal FROM likes x JOIN actors a ON a.id=x.actor_id JOIN videos v ON v.id=x.video_id WHERE x.run_id=? ORDER BY a.ordinal,v.ordinal", (run_id,)).fetchall()
                items.extend("l:" + json.dumps(list(x)) for x in vals)
            else:
                vals = db.execute("SELECT a.ordinal,c.ordinal FROM follows x JOIN actors a ON a.id=x.actor_id JOIN creators c ON c.id=x.creator_id WHERE x.run_id=? ORDER BY a.ordinal,c.ordinal", (run_id,)).fetchall()
                items.extend("f:" + json.dumps(list(x)) for x in vals)
        return hashlib.sha256("\n".join(items).encode()).hexdigest()

    def accounts(self, run_id, offset=0, limit=50, query="", status="all"):
        limit, offset = max(1, min(100, int(limit))), max(0, int(offset))
        like = f"%{query.strip()}%"
        stage_filter = ""
        params: list[Any] = [run_id, like, like]
        if status not in {"all", "queued", "email_created", "email_verified", "social_created", "completed"}:
            raise ValueError("Trạng thái không hợp lệ")
        if status != "all":
            stage_filter = "AND a.stage=?"
            params.append(status)
        params.extend([limit, offset])
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM actors a WHERE a.run_id=? AND (a.fake_email LIKE ? OR a.fake_handle LIKE ?) {stage_filter}", params[:-2]).fetchone()[0]
            rows = db.execute(f"""SELECT a.id,a.ordinal,a.fake_email,a.fake_handle,a.stage,a.created_at,
              (SELECT COUNT(*) FROM views v WHERE v.actor_id=a.id) views,
              (SELECT COUNT(*) FROM likes l WHERE l.actor_id=a.id) likes,
              (SELECT COUNT(*) FROM follows f WHERE f.actor_id=a.id) follows
              FROM actors a WHERE a.run_id=? AND (a.fake_email LIKE ? OR a.fake_handle LIKE ?) {stage_filter}
              ORDER BY a.ordinal LIMIT ? OFFSET ?""", params).fetchall()
            return {"total": total, "offset": offset, "limit": limit, "accounts": [dict(r) for r in rows]}

    def mailbox(self, run_id, actor_id):
        with self._db() as db:
            row = db.execute("SELECT fake_email FROM actors WHERE id=? AND run_id=?", (actor_id, run_id)).fetchone()
            if not row:
                raise KeyError("Không tìm thấy hộp thư mô phỏng")
            mails = [dict(m) for m in db.execute("SELECT subject,body,code,created_at FROM local_mail WHERE actor_id=? ORDER BY id", (actor_id,))]
            return {"simulation_only": True, "address": row["fake_email"], "messages": mails}

    def compare(self, left_id, right_id):
        with self._db() as db:
            left = db.execute("SELECT * FROM runs WHERE id=?", (left_id,)).fetchone()
            right = db.execute("SELECT * FROM runs WHERE id=?", (right_id,)).fetchone()
            if not left or not right:
                raise KeyError("Không tìm thấy thử nghiệm để so sánh")
            if left["status"] != "completed" or right["status"] != "completed":
                raise ValueError("Cần hoàn tất cả hai thử nghiệm để so sánh")
            lm, rm = self._metrics(db, left_id), self._metrics(db, right_id)
            keys = ("emails", "social_accounts", "views", "likes", "follows", "watch_seconds")
            same_config = {k: v for k, v in json.loads(left["config"]).items() if k != "name"} == {k: v for k, v in json.loads(right["config"]).items() if k != "name"}
            lfp, rfp = self._fingerprint(db, left_id), self._fingerprint(db, right_id)
            return {"left": self._summary(db, left), "right": self._summary(db, right),
                    "delta": {k: rm[k] - lm[k] for k in keys}, "same_config": same_config,
                    "same_result": lfp == rfp, "left_fingerprint": lfp, "right_fingerprint": rfp}

    def report(self, run_id):
        state = self.public_state(run_id)
        if not state["selected"]:
            raise KeyError("Không tìm thấy thử nghiệm")
        return {"simulation_only": True, "model_version": MODEL_VERSION, "run": state["selected"]}

    def events_csv(self, run_id):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError("Không tìm thấy thử nghiệm")
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["simulated_time", "step", "event", "synthetic_account", "target", "details"])
            for row in db.execute("""SELECT e.sim_time,e.step,e.kind,a.fake_handle,e.target_label,e.message
              FROM events e LEFT JOIN actors a ON a.id=e.actor_id WHERE e.run_id=? ORDER BY e.id""", (run_id,)):
                writer.writerow(list(row))
            return output.getvalue()

    def accounts_csv(self, run_id):
        first = self.accounts(run_id, 0, 100)
        accounts = first["accounts"]
        for offset in range(100, first["total"], 100):
            accounts.extend(self.accounts(run_id, offset, 100)["accounts"])
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=["fake_handle", "fake_email", "stage", "views", "likes", "follows"])
        writer.writeheader()
        for account in accounts:
            writer.writerow({k: account[k] for k in writer.fieldnames})
        return output.getvalue()
