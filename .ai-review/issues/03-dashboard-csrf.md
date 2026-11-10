## Summary
The Flask dashboard accepts state-changing requests from any web origin and any `Host` header. Any web page the DM visits while the dashboard is running can delete or overwrite campaigns with a cross-site form POST. A DNS-rebinding page can go further and drive `/play/turn`, which runs a headless `claude -p` agent with `Bash` and `Write` allowed.

## Context
`dashboard.py` is a single Flask app, run locally with `python3 dashboard.py`, bound to `127.0.0.1:5000` by default. It has no authentication; the startup banner says so for non-loopback binds. Loopback is not a security boundary against a browser on the same machine: any site can submit a form to `http://127.0.0.1:5000/...`, and DNS rebinding makes the attacker's page same-origin with the dashboard.

Relevant code (pinned to 2965eb7):
- [`dashboard.py#L232-L234`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L232-L234): `_refresh_before_request`, the only `before_request` hook. It does no origin or host checks.
- [`dashboard.py#L2777-L2795`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L2777-L2795): `campaign_delete`, `POST /campaigns/<slug>/delete`, takes no body and calls `shutil.rmtree` (via `_arch.delete_campaign`).
- [`dashboard.py#L2827-L2848`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L2827-L2848): `campaign_import`, a multipart POST. With `force` set it `rmtree`s an existing campaign and replaces it.
- [`dashboard.py#L2712-L2726`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L2712-L2726): `campaign_switch`. [`#L2729-L2774`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L2729-L2774): `campaign_create` (form POST).
- [`dashboard.py#L6936-L6951`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L6936-L6951): `play_turn` streams `_dm.stream_turn(msg)`.
- [`tools/dm_session.py#L630`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dm_session.py#L630): `_ALLOWED_TOOLS = "mcp__ttrpg__*,Read,Write,Edit,Bash"`, passed as `--allowedTools` to the spawned CLI at [`#L1114`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dm_session.py#L1114).
- Other mutating routes: `DELETE /api/sessions/<sid>`, `DELETE /api/images/<filename>`, `PUT /api/tts/voices`, `POST /api/tts/synthesize` (spends API credit), `POST /play/*`, `POST /api/party/reorder`, `POST /campaigns/<slug>/banner`, `POST /combat-fog-*`, `POST /api/holdings/...`, `POST /api/audit_*`.

## Evidence
Reading the code: no route checks `Origin`, `Referer`, `Sec-Fetch-Site`, a CSRF token, or `Host`. A cross-site page containing
```html
<form method="POST" action="http://127.0.0.1:5000/campaigns/greyhawk/delete"></form><script>document.forms[0].submit()</script>
```
is a simple (non-preflighted) request, so the browser sends it and `campaign_delete` runs `rmtree` on `campaigns/greyhawk` (unless it is the active campaign). `/play/turn` reads `request.get_json`, which needs `Content-Type: application/json`, so a plain cross-site request is preflighted and blocked. A DNS-rebinding origin is same-origin, though, and passes. The `Host` header is never checked.

## Proposed design
Add one `@app.before_request` guard, registered **before** `_refresh_before_request`:

```python
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

def _allowed_hosts() -> set[str]:
    # host[:port] values the dashboard answers to
    ...

@app.before_request
def _reject_foreign_requests():
    host = (request.host or "").lower()
    if host not in _ALLOWED_HOSTS:
        abort(403)            # blocks DNS rebinding for every method
    if request.method in _SAFE_METHODS:
        return None
    origin = request.headers.get("Origin")
    if origin is None:
        # Fall back to Sec-Fetch-Site, then Referer; non-browser clients
        # (curl, scripts) that send none of these are allowed.
        site = request.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            abort(403)
        ref = request.headers.get("Referer")
        if ref and urlsplit(ref).netloc.lower() != host:
            abort(403)
        return None
    if urlsplit(origin).netloc.lower() != host:
        abort(403)
```

`_ALLOWED_HOSTS` is computed at startup in `__main__` from `--host` and `--port`. It always contains `localhost:<port>`, `127.0.0.1:<port>` and `[::1]:<port>`. It also contains `<args.host>:<port>` when that is not a wildcard. Add a new repeatable `--allow-host` flag for LAN or reverse-proxy names. When the app is imported without `__main__` (tests, WSGI), default to the loopback set for port 5000, and let tests override it by setting `app.config["ALLOWED_HOSTS"]`.

Why this beats the alternatives: CSRF tokens would need changes to every form and `fetch` call in the inlined HTML and JS (hundreds of call sites in an 11k-line file). Browsers already send `Origin` on every non-GET request, so the check needs no frontend changes. A Host allowlist is the standard DNS-rebinding defence. Requiring auth is a product decision and is out of scope.

## Implementation steps
1. In `dashboard.py`, add `from urllib.parse import urlsplit` if it is not already imported.
2. Add `_SAFE_METHODS`, `_loopback_hosts(port: int) -> set[str]` and `_reject_foreign_requests()` just above `_refresh_before_request` (around L230). Read `app.config.get("ALLOWED_HOSTS")`, falling back to `_loopback_hosts(5000)`.
3. In the `if __name__ == "__main__":` block (around L11325–L11369), add the `--allow-host` argument (`action="append"`, default `[]`). After `parse_args`, set `app.config["ALLOWED_HOSTS"] = _loopback_hosts(args.port) | {f"{h}:{args.port}" for h in [args.host, *args.allow_host] if h not in ("", "0.0.0.0", "::", "*")}`. Note that Flask's `request.host` includes the port.
4. Return a JSON 403 body (`{"error": "cross-origin request refused"}`) for paths starting with `/api/` or `/play/`, and plain `abort(403)` otherwise.
5. Document the new flag and the behaviour in the README's Running section.

## Acceptance criteria
- [ ] `app.test_client().post("/campaigns/x/delete", headers={"Origin": "http://evil.example"})` returns 403, and the campaign directory still exists.
- [ ] The same POST with `Origin: http://127.0.0.1:5000` and `Host: 127.0.0.1:5000` reaches the handler (it returns 404 or 409, not 403).
- [ ] `GET /party` with `Host: evil.example:5000` returns 403. `GET /party` with `Host: localhost:5000` returns 200.
- [ ] A POST with no `Origin`, `Referer` or `Sec-Fetch-Site` (a curl-style client) is allowed.
- [ ] Manually, `python3 dashboard.py` then the /campaigns page: switching, creating and deleting campaigns still works in the browser.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
Add `tests/test_dashboard_origin.py`, using `dashboard.app.test_client()` with `app.config["ALLOWED_HOSTS"] = {"127.0.0.1:5000", "localhost:5000"}`. Use `TempCampaign` from #1 so importing `dashboard` has a campaign to load; if module import needs `cfg`, set it as `dashboard.cfg` does. Cases:
- foreign Origin on POST, DELETE and PUT gives 403
- same Origin gives no 403
- Referer-only foreign gives 403; `Sec-Fetch-Site: cross-site` gives 403
- no headers gives an allowed request
- a foreign Host on GET gives 403
- `--allow-host` value accepted (unit-test the host-set builder)

## Out of scope
- Do not add login or authentication. Do not change `_ALLOWED_TOOLS` in `tools/dm_session.py`.
- Do not add CSRF tokens to templates.
- Do not change the debug/loopback defaults.

## Risks and gotchas
- `request.host` includes the port. Compare like with like.
- Users who reach the dashboard through a LAN IP or reverse proxy will get 403 until they pass `--allow-host`. Say so in the README and in the 403 body.
- Werkzeug's debugger (on by default for loopback binds) serves `/?__debugger__=...` GETs. The Host check covers it too, which is intended.
- Register the guard before `_refresh_before_request`: Flask runs `before_request` functions in registration order, and a rejected request should not reload config.

## Dependencies
Blocked by: #1

## Metadata
Type: security | Severity: high | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
