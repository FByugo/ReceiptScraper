"""Every outcome the tool can report, driven by a real local HTTP server."""
import http.server
import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from receipt_scraper import core
from receipt_scraper.cli import app

LONG = "<p>" + "Real readable sentence about the product. " * 20 + "</p>"
ROUTES = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private\n"),
    "/ok": (200, "text/html", f"<html><head><title>Good Page</title><meta name='description' content='A real page'></head><body><h1>Hello</h1>{LONG}<script>var hidden='no'</script><noscript>hiddenns</noscript><template>hiddentpl</template></body></html>"),
    "/forbidden-plain": (403, "text/html", "<html><body>" + "An ordinary page body with nothing special in it. " * 10 + "</body></html>"),
    "/thin": (200, "text/html", "<html><body><div id='app'></div><script>render()</script></body></html>"),
    "/forbidden": (403, "text/html", "<html><body>Access denied</body></html>"),
    "/captcha": (200, "text/html", "<html><title>Just a moment...</title><body>Verify you are human</body></html>"),
    "/private/page": (200, "text/html", f"<html><body>{LONG}</body></html>"),
    "/empty-200": (200, "text/html", ""),
    "/plain-403": (403, "text/plain", "Please set a user-agent and respect our robot policy."),
    "/doc.pdf": (200, "application/pdf", "%PDF-1.4 fake"),
    "/gone": (404, "text/html", "<html><body>" + "Not found here. " * 30 + "</body></html>"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/old":
            self.send_response(301); self.send_header("Location", "/ok"); self.end_headers(); return
        status, ctype, body = ROUTES.get(self.path, (404, "text/plain", "nope"))
        data = body.encode()
        self.send_response(status); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def outcomes(summary_dir):
    m = json.loads((Path(summary_dir) / "manifest.json").read_text())
    return {p["url"].split("/", 3)[-1]: p for p in m["pages"]}


def test_every_outcome(base, tmp_path):
    urls = [f"{base}/{p}" for p in ["ok", "thin", "forbidden", "forbidden-plain", "captcha", "private/page", "doc.pdf", "gone", "old"]]
    urls.append("http://127.0.0.1:1/dead")  # nothing listens on port 1
    s = core.run(urls, tmp_path, delay=0)
    got = outcomes(tmp_path)
    assert got["ok"]["outcome"] == "ok" and got["ok"]["title"] == "Good Page"
    assert got["ok"]["description"] == "A real page" and got["ok"]["h1"] == "Hello"
    assert "hidden" not in (tmp_path / "receipts" / got["ok"]["text_file"]).read_text()  # script text stripped
    assert got["forbidden-plain"]["outcome"] == "blocked"  # status alone, no marker text
    assert got["thin"]["outcome"] == "thin"
    assert got["forbidden"]["outcome"] == "blocked" and got["forbidden"]["status"] == 403
    assert got["captcha"]["outcome"] == "blocked" and got["captcha"]["status"] == 200
    assert got["private/page"]["outcome"] == "robots" and got["private/page"]["receipt"] == ""
    assert got["doc.pdf"]["outcome"] == "not_html" and got["doc.pdf"]["receipt"]
    assert got["gone"]["outcome"] == "error" and got["gone"]["status"] == 404
    assert got["old"]["outcome"] == "ok" and got["old"]["final_url"].endswith("/ok")
    assert got["dead"]["outcome"] == "error" and "ConnectionError" in got["dead"]["note"]
    assert s.total == 10 and sum(s.counts.values()) == 10
    assert (tmp_path / "results.csv").read_text().count("\n") == 11  # header + 10 rows


def test_plain_text_refusal_is_blocked_not_a_document(base, tmp_path):
    core.run([f"{base}/plain-403"], tmp_path, delay=0)
    assert outcomes(tmp_path)["plain-403"]["outcome"] == "blocked"


def test_empty_body_is_named_and_not_parsed(base, tmp_path, caplog):
    core.run([f"{base}/empty-200"], tmp_path, delay=0)
    rec = outcomes(tmp_path)["empty-200"]
    assert rec["outcome"] == "thin" and rec["note"] == "HTTP 200, empty body"
    assert "could not be decoded" not in caplog.text


def test_ignore_robots_fetches_the_disallowed_page(base, tmp_path):
    core.run([f"{base}/private/page"], tmp_path, delay=0, respect_robots=False)
    assert outcomes(tmp_path)["private/page"]["outcome"] == "ok"


def test_verify_passes_then_catches_tampering(base, tmp_path):
    core.run([f"{base}/ok", f"{base}/captcha"], tmp_path, delay=0)
    assert core.verify(tmp_path) == []
    receipt = tmp_path / "receipts" / outcomes(tmp_path)["ok"]["receipt"]
    receipt.write_bytes(receipt.read_bytes() + b" ")
    problems = core.verify(tmp_path)
    assert len(problems) == 1 and "hash mismatch" in problems[0]
    receipt.unlink()
    assert "missing receipt" in core.verify(tmp_path)[0]


def test_url_list_parsing(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("# comment\n\nexample.com\nhttps://a.test/x\nhttps://a.test/x\n")
    assert core.read_url_list(f) == ["https://example.com", "https://a.test/x"]


def test_cli_exit_codes(base, tmp_path):
    r = CliRunner()
    good = tmp_path / "good.txt"; good.write_text(f"{base}/ok\n")
    res = r.invoke(app, ["run", str(good), "--out", str(tmp_path / "o1"), "--delay", "0"])
    assert res.exit_code == 0 and "ok 1" in res.output
    assert r.invoke(app, ["verify", str(tmp_path / "o1")]).exit_code == 0
    bad = tmp_path / "bad.txt"; bad.write_text(f"{base}/forbidden\n")
    assert r.invoke(app, ["run", str(bad), "--out", str(tmp_path / "o2"), "--delay", "0"]).exit_code == 1
    empty = tmp_path / "e.txt"; empty.write_text("# nothing\n")
    assert r.invoke(app, ["run", str(empty), "--out", str(tmp_path / "o3")]).exit_code == 2
