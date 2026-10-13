"""server.py — local server for the Question Image Extractor.

Serves web/ at http://127.0.0.1:8000 and uploads question images to CMS360:
for each image it uploads the file to CMS360 storage, creates a child group
question with the image in its Paragraph, and links that child to the parent.

The CMS360 token is read from secrets/.env (CMS_TOKEN=...) on every request,
so pasting a fresh token there takes effect without restarting. The token is
never sent to the browser.

Usage:
    python server.py            (then open http://127.0.0.1:8000)
    python server.py --port 8080
"""

import argparse
import base64
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
ENV_PATH = os.path.join(HERE, "secrets", ".env")  # replaced by --env
OUTPUT_DIR = os.path.join(HERE, "output")
LOG_PATH = os.path.join(OUTPUT_DIR, "upload-log.csv")

DEFAULT_API = "https://api.10minuteschool.com"
# Public client key the CMS360 web app sends with every file upload.
TENMS_USER_KEY = "base64:dGVubXM6MTAxMA=="
BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://cms-360.10minuteschool.com",
    "Referer": "https://cms-360.10minuteschool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
}
LOG_FIELDS = ["time", "paper", "parent_id", "parent_title", "question", "file", "child_id", "question_id", "image_url", "status", "error"]


class CmsError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def read_env(name):
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            m = re.search(rf"^{name}=(.+)$", f.read(), re.M)
    except FileNotFoundError:
        raise CmsError("secrets/.env is missing. Create it with a line CMS_TOKEN=<your token>.", 400)
    return m.group(1).strip() if m else ""


def read_token():
    token = read_env("CMS_TOKEN")
    if not token:
        raise CmsError("No CMS_TOKEN found in secrets/.env.", 400)
    return token


def content_url(path):
    return (read_env("CMS_API_BASE") or DEFAULT_API).rstrip("/") + "/content-service/api/v1" + path


def upload_url():
    return (read_env("CMS_API_BASE") or DEFAULT_API).rstrip("/") + "/s3-manager/api/v1/files/services/upload"


def token_expiry(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("exp")
    except Exception:
        return None


def cms_request(method, url, body=None, headers=None, raw=None):
    token = read_token()
    exp = token_expiry(token)
    if exp and exp < time.time():
        raise CmsError("The CMS360 token has expired. Paste a fresh one into secrets/.env.", 401)
    h = dict(BROWSER_HEADERS)
    h["Authorization"] = "Bearer " + token
    data = raw
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            raise CmsError("CMS360 rejected the token (401). Copy a fresh _t cookie value into secrets/.env.", 401)
        raise CmsError(f"CMS360 answered {e.code}: {detail}", 502)
    except urllib.error.URLError as e:
        raise CmsError(f"Couldn't reach CMS360: {e.reason}", 502)
    try:
        return json.loads(text)
    except ValueError:
        raise CmsError(f"CMS360 sent an unexpected reply: {text[:200]}", 502)


def unwrap(resp):
    """CMS360 replies look like {"code": ..., "data": {...}}; return data or raise on errors."""
    if isinstance(resp, dict):
        if resp.get("errors"):
            raise CmsError(f"CMS360 refused it: {json.dumps(resp['errors'], ensure_ascii=False)[:300]}")
        if "data" in resp:
            return resp["data"]
    return resp


def get_group(group_id):
    return unwrap(cms_request("GET", content_url(f"/group-questions/{group_id}?with_attribute_details=true")))


def get_children(group_id):
    """Children linked to a parent, as CMS360 lists them under the parent (only active ones show)."""
    data = unwrap(cms_request("GET", content_url(f"/group-questions/{group_id}/map-group-questions?with_attribute_details=true")))
    if isinstance(data, dict) and isinstance(data.get("questions"), list):
        return data["questions"]
    return data if isinstance(data, list) else []


def upload_file(filename, content, content_type="image/png", bucket="10ms-content-service", key="resources"):
    """Upload a file to CMS360 storage and return its CDN link (same call the CMS360 web app makes)."""
    boundary = "----QIE" + uuid.uuid4().hex
    parts = []
    for name, value in (("bucket", bucket), ("acl", "public-read"), ("key", key)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "question.png"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    resp = cms_request("POST", upload_url(), raw=b"".join(parts), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-tenms-user-key": TENMS_USER_KEY,
    })
    data = unwrap(resp)
    url = isinstance(data, dict) and (data.get("cdn_url") or data.get("file_url"))
    if not url:
        raise CmsError(f"The upload of {filename} gave no link back: {json.dumps(resp, ensure_ascii=False)[:200]}")
    return url


def paragraph_with_image(image_url):
    """The Paragraph box content CMS360's editor saves when a picture is added with its + button.

    Returns (html, attachments): the editor's JSON and its HTML are uploaded the way the CMS360 form does
    (editor.json / myfile.html in tenms-content-service) and attached as group_question_paragraph."""
    block_id = uuid.uuid4().hex[:10]
    editor = {"time": int(time.time() * 1000), "blocks": [{
        "id": block_id, "type": "image",
        "data": {"file": {"url": image_url}, "caption": "", "withBorder": False, "stretched": False, "withBackground": False},
    }]}
    # editorjs-html output for that block, exactly as the CMS360 form builds it.
    html = f'<figure class="fig-img"><img class="img   " src="{image_url}" alt=""><figcaption class="fig-cap"></figcaption></figure>'
    html_url = upload_file("myfile.html", f"<html><body>{html}</body></html>".encode("utf-8"), "text/html",
                           bucket="tenms-content-service", key="html")
    json_url = upload_file("editor.json", json.dumps(editor).encode("utf-8"), "application/json",
                           bucket="tenms-content-service", key="json")
    attachments = [
        {"identifier": "group_question_paragraph", "file_type": "html", "file_url": html_url, "action": "store"},
        {"identifier": "group_question_paragraph", "file_type": "json", "file_url": json_url, "action": "store"},
    ]
    return html, attachments


def create_child(parent_id, title, internal_title, marks, status, image_url, order_index):
    # The picture goes inside the Paragraph box (editor content), not as a separate paragraph image,
    # so it shows in the box when the child is opened for editing in CMS360.
    html, attachments = paragraph_with_image(image_url)
    body = {
        "title": title,
        "type": "cq",
        "internal_title": internal_title,
        "options_label": "",
        "questions_label": "",
        "paragraph": html.replace('"', "'"),
        "status": status,
        "explanation": "",
        "attachments": attachments,
        "content_attributes": [],
        "marks": marks,
    }
    created = unwrap(cms_request("POST", content_url("/group-questions"), body=body))
    # The CMS360 web app treats a "code" or "errors" field inside data as a failed save.
    if isinstance(created, dict) and (created.get("code") or created.get("errors")):
        raise CmsError(f"CMS360 didn't create the child: {json.dumps(created, ensure_ascii=False)[:300]}")
    child_id = isinstance(created, dict) and created.get("id")
    if not child_id:
        raise CmsError(f"CMS360 didn't return the new child's id: {json.dumps(created, ensure_ascii=False)[:200]}")
    # Same body as "Add Child Group Question" → "+" in CMS360. Without order_index/min/max/marks
    # CMS360 answers 200 but the child never appears under the parent. min and max follow the order index.
    link = unwrap(cms_request("POST", content_url(f"/group-questions/{parent_id}/map-group-questions"), body={
        "order_index": order_index, "model_type": "group_question", "model_id": child_id,
        "min": order_index, "max": order_index, "marks": marks,
    }))
    if not (isinstance(link, dict) and str(link.get("model_id")) == str(child_id)):
        raise CmsError(f"Child {child_id} was created but CMS360 didn't confirm the link to parent {parent_id}: "
                       f"{json.dumps(link, ensure_ascii=False)[:200]}")
    return child_id


def save_token(token):
    """Write CMS_TOKEN into the env file, keeping its other lines."""
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        raise CmsError("That doesn't look like a CMS360 token. Copy the whole value of the _t cookie.", 400)
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith("CMS_TOKEN=")]
    lines.append("CMS_TOKEN=" + token)
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return token_expiry(token)


def token_works():
    """One read-only call: list a single group question."""
    cms_request("GET", content_url("/group-questions?page=1&limit=1"))
    return True


def create_inner_question(child_id, title, marks, modality="m9"):
    """Create the question inside a child (what "Add Question" does) and make sure it's linked to the child."""
    body = {
        "title": title, "marks": marks, "status": "active", "type": modality,
        "image": "", "video": "", "audio": "", "explanation": "",
        "content_attributes": [], "options": [], "group_question_id": child_id,
    }
    created = unwrap(cms_request("POST", content_url("/questions"), body=body))
    qid = isinstance(created, dict) and created.get("id")
    if not qid:
        raise CmsError(f"CMS360 didn't return the new question's id: {json.dumps(created, ensure_ascii=False)[:200]}")
    # CMS360 links the question to the child by itself (from group_question_id), but with order 0 and no
    # min/max. Posting the link again updates that same link: order 1, min = max = the order index,
    # marks = the child's marks.
    link = unwrap(cms_request("POST", content_url(f"/group-questions/{child_id}/map-group-questions"), body={
        "order_index": 1, "max": 1, "min": 1, "marks": marks, "model_type": "question", "model_id": qid,
    }))
    if not (isinstance(link, dict) and str(link.get("model_id")) == str(qid)):
        raise CmsError(f"Question {qid} was created but CMS360 didn't confirm it under child {child_id}.")
    return qid


def safe_name(s, fallback):
    s = re.sub(r'[\\/:*?"<>|]+', "", s or "").strip()
    return s or fallback


def append_log(row):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != LOG_FIELDS:
                # An older log without the newer columns: rewrite it once with the current header.
                old_rows = list(reader)
                with open(LOG_PATH, "w", newline="", encoding="utf-8-sig") as out:
                    w = csv.DictWriter(out, fieldnames=LOG_FIELDS)
                    w.writeheader()
                    for r in old_rows:
                        w.writerow({k: r.get(k, "") for k in LOG_FIELDS})
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_FIELDS})


def read_log(parent_id):
    """Successful uploads to this parent, so the page can skip questions already sent."""
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, newline="", encoding="utf-8-sig") as f:
        return [
            {"paper": row["paper"], "question": row["question"], "child_id": row["child_id"], "time": row["time"]}
            for row in csv.DictReader(f)
            if row.get("parent_id") == str(parent_id) and row.get("status") == "uploaded"
        ]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if not self.path.startswith("/api/"):
            return super().do_GET()
        try:
            path, _, query = self.path.partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            if path == "/api/cms/status":
                try:
                    exp = token_expiry(read_token())
                    token_works()
                    return self.send_json(200, {"ok": True, "token_expires": exp})
                except CmsError as e:
                    return self.send_json(200, {"ok": True, "token_expires": token_expiry(read_env("CMS_TOKEN")), "token_error": str(e)})
            if path == "/api/cms/parent":
                pid = int(params.get("id", "0"))
                if pid <= 0:
                    raise CmsError("Enter a parent group question ID.", 400)
                group = get_group(pid)
                children = get_children(pid)
                linked = {str(c.get("model_id")) for c in children}
                return self.send_json(200, {
                    "id": pid,
                    "title": group.get("title") if isinstance(group, dict) else None,
                    "type": group.get("type") if isinstance(group, dict) else None,
                    "children": len(children),
                    "max_order": max([c.get("order_index") or 0 for c in children] or [0]),
                    # Only count earlier uploads whose child is really under this parent now.
                    "uploaded": [r for r in read_log(pid) if r["child_id"] in linked],
                })
            self.send_json(404, {"error": "Unknown address."})
        except CmsError as e:
            self.send_json(e.status, {"error": str(e)})
        except ValueError:
            self.send_json(400, {"error": "The parent ID must be a number."})

    def do_POST(self):
        if self.path == "/api/cms/token":
            try:
                exp = save_token(str(self.read_json().get("token") or ""))
                try:
                    token_works()
                    return self.send_json(200, {"ok": True, "token_expires": exp, "works": True})
                except CmsError as e:
                    return self.send_json(200, {"ok": True, "token_expires": exp, "works": False, "error": str(e)})
            except CmsError as e:
                return self.send_json(e.status, {"error": str(e)})
        if self.path != "/api/cms/upload":
            return self.send_json(404, {"error": "Unknown address."})
        row = {"time": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            req = self.read_json()
            pid = int(req.get("parentId") or 0)
            if pid <= 0:
                raise CmsError("Missing parent ID.", 400)
            question = str(req.get("question") or "")
            filename = safe_name(req.get("filename"), f"Ques {question}.png")
            paper = safe_name(req.get("paper"), "Question paper")
            content = base64.b64decode(req.get("image") or "")
            if not content:
                raise CmsError("No image was sent.", 400)
            row.update(paper=paper, parent_id=pid, parent_title=req.get("parentTitle", ""), question=question, file=filename)

            # Keep a local copy next to the log.
            folder = os.path.join(OUTPUT_DIR, paper)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, filename), "wb") as f:
                f.write(content)

            image_url = upload_file(filename, content)
            row["image_url"] = image_url
            marks = req.get("marks")
            order = req.get("orderIndex")
            child_id = create_child(
                pid,
                title=str(req.get("title") or filename.rsplit(".", 1)[0]),
                internal_title=str(req.get("internalTitle") or req.get("title") or filename.rsplit(".", 1)[0]),
                marks=int(marks) if str(marks).isdigit() else 1,
                status="inactive" if req.get("status") == "inactive" else "active",
                image_url=image_url,
                order_index=int(order) if str(order).isdigit() else 1,
            )
            row.update(child_id=child_id, status="uploaded")
            question_id = None
            if req.get("innerQuestion"):
                try:
                    question_id = create_inner_question(child_id, str(req.get("innerTitle") or "প্রশ্নগুলোর উত্তর দাও"),
                                                        marks=int(marks) if str(marks).isdigit() else 1)
                    row["question_id"] = question_id
                except CmsError as e:
                    # The child exists and is linked; only its inner question is missing.
                    row["error"] = f"inner question failed: {e}"
                    append_log(row)
                    return self.send_json(502, {"error": f"Child {child_id} was created, but its question wasn't: {e}",
                                                "child_id": child_id, "image_url": image_url})
            append_log(row)
            self.send_json(200, {"ok": True, "child_id": child_id, "question_id": question_id, "image_url": image_url})
        except CmsError as e:
            row.update(status="failed", error=str(e))
            append_log(row)
            self.send_json(e.status, {"error": str(e), "image_url": row.get("image_url")})
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": f"Bad request: {e}"})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--env", help="Use a different env file (for testing)")
    args = p.parse_args()
    if args.env:
        global ENV_PATH
        ENV_PATH = os.path.abspath(args.env)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Question Image Extractor running at http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
