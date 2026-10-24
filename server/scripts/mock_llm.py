"""OpenAI 호환 가짜 LLM 서버 (mock 모드 E2E용, 01-plan PR-15). 표준 라이브러리만 쓴다.

GPU 없이 airgap 구성을 확인할 때 vLLM 자리에 띄운다(docker-compose.airgap.mock.yaml).
POST /v1/chat/completions는 요청의 response_format(json_schema)에 맞는 값을 채워 돌려준다. 내용은 의미 없는 고정 문구다.
GET /health, GET /v1/models도 응답한다. 실제 요약 품질 확인용이 아니다.

사용: python -m scripts.mock_llm [--host 0.0.0.0] [--port 8000]
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def fake_value(schema: dict, defs: dict, name: str = "value"):
    """JSON Schema에 맞는 고정 값을 만든다. 문자열은 "(mock) 필드명", 숫자는 0, 배열은 원소 하나."""
    if "$ref" in schema:
        return fake_value(defs[schema["$ref"].split("/")[-1]], defs, name)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return fake_value(schema[key][0], defs, name)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {key: fake_value(value, defs, key) for key, value in schema.get("properties", {}).items()}
    if kind == "array":
        return [fake_value(schema.get("items", {}), defs, name)]
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return f"(mock) {name}"


def completion(body: dict) -> dict:
    schema = (body.get("response_format") or {}).get("json_schema", {}).get("schema")
    content = json.dumps(fake_value(schema, schema.get("$defs", {})), ensure_ascii=False) if schema else "(mock) response"
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model") or "mock",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, {"status": "ok"})
        if path == "/v1/models":
            return self._send(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        return self._send(404, {"error": f"mock llm: GET {path}"})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if path == "/v1/chat/completions":
            return self._send(200, completion(body))
        return self._send(404, {"error": f"mock llm: POST {path}"})

    def log_message(self, fmt, *args):
        print(f"[mock-llm] {self.address_string()} {fmt % args}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible mock LLM for airgap mock-mode E2E")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mock-llm] listening on {args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
