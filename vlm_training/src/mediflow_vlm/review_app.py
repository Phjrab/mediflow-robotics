from __future__ import annotations

import argparse
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from .review_store import ReviewStore


def create_app(store: ReviewStore) -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.review_store = store  # type: ignore[attr-defined]

    @app.get("/")
    def index():
        return render_template("review.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "manifest": str(store.manifest_path)})

    @app.get("/api/items")
    def items():
        return jsonify({"items": store.items(), "summary": store.summary()})

    @app.get("/api/images/<sample_id>")
    def image(sample_id: str):
        row = store.by_id.get(sample_id)
        if row is None:
            return jsonify({"ok": False, "error": "sample not found"}), 404
        return send_file(store.image_path(row), conditional=True)

    @app.get("/api/videos/<sample_id>")
    def video(sample_id: str):
        row = store.by_id.get(sample_id)
        if row is None:
            return jsonify({"ok": False, "error": "sample not found"}), 404
        path = store.video_path(row)
        if path is None:
            return jsonify({"ok": False, "error": "video not found"}), 404
        return send_file(path, conditional=True)

    @app.post("/api/reviews/<sample_id>")
    def review(sample_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            item = store.record(
                sample_id=sample_id,
                status=str(payload.get("status", "")),
                medicine_id=str(payload.get("medicine_id", "")),
                orientation=str(payload.get("orientation", "")),
                grasp_region=payload.get("grasp_region"),
                note=str(payload.get("note", "")),
            )
        except KeyError:
            return jsonify({"ok": False, "error": "sample not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "item": item, "summary": store.summary()})

    @app.post("/api/export")
    def export():
        return jsonify({"ok": True, "export": store.export_approved()})

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local MediFlow dataset review UI.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--manifest", type=Path, default=Path("dataset_v2/review_manifest.jsonl")
    )
    parser.add_argument("--decisions", type=Path, default=Path("dataset_v2/review_decisions.jsonl"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = ReviewStore(args.project_root, args.manifest, args.decisions)
    app = create_app(store)
    print(f"MediFlow review UI: http://{args.host}:{args.port}/", flush=True)
    print(f"Review decisions: {store.decisions_path}", flush=True)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
