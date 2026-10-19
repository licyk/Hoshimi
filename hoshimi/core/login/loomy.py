"""Loomy: WeChat QR login on a local page, then iFlytek's signed account API.

The upstream client intercepts a WeChat redirect that is not reachable in a
browser. The flow therefore follows dsh-our-free-model's two-step shape: it
fetches the QR tied to one server-side polling UUID, serves that QR from a
loopback page, and exchanges the resulting WeChat code for Loomy's session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError

ACCOUNT_BASE = "https://account.xfinfr.com"
WECHAT_AUTH_URL = "https://open.weixin.qq.com/connect/qrconnect"
WECHAT_QR_URL = "https://open.weixin.qq.com/connect/qrcode"
WECHAT_POLL_URL = "https://long.open.weixin.qq.com/connect/l/qrconnect"
WECHAT_APP_ID = "wx18d60be432287cf8"
WECHAT_REDIRECT_URI = "https://loomy.xunfei.cn/oauth/wechat/callback"
WECHAT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

ACCOUNT_KEY_ID = "2thryby66wxi53sk"
ACCOUNT_KEY_SECRET = "zsak6eadrbawz683wf5r3m2snrwj868r"
ACCOUNT_APP_ID = "GM3LOOMY"
SESSION_TTL_SECONDS = 1_209_600

QR_PATH = "/wechat/qr"
POLL_PATH = "/wechat/poll"
COMPLETE_PATH = "/wechat/complete"
_UUID_RE = re.compile(r"(?:/connect/qrcode/|l/qrconnect\?uuid=)([A-Za-z0-9_=+/-]{6,64})")


def _auth_url(state: str) -> str:
    return (
        f"{WECHAT_AUTH_URL}?appid={quote(WECHAT_APP_ID)}&redirect_uri={quote(WECHAT_REDIRECT_URI)}"
        "&response_type=code&scope=snsapi_login" + f"&state={quote(state)}#wechat_redirect"
    )


def _extract_uuid(html: str) -> str:
    match = _UUID_RE.search(html)
    return match.group(1) if match else ""


def _escape_rfc3986(value: str) -> str:
    return quote(value, safe="").replace("!", "%21").replace("'", "%27").replace("(", "%28").replace(")", "%29").replace("*", "%2A")


def _account_body(param: dict[str, Any]) -> str:
    body = {
        "base": {
            "appid": ACCOUNT_APP_ID,
            "modelid": "Web",
            "version": "1.0.0",
            "devid": "web",
            "ua": "Loomy|Desktop|Electron|macOS",
            "traceid": uuid.uuid4().hex,
        },
        "param": param,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _account_headers(path: str, body: str) -> dict[str, str]:
    digest = base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("ascii") if body else ""
    date = formatdate(time.time(), usegmt=True)
    nonce = str(uuid.uuid4())
    string_to_sign = "\n".join(["POST", path, "", digest, "application/json", date, nonce, "", ""])  # noqa: FLY002
    signature = base64.b64encode(hmac.new(ACCOUNT_KEY_SECRET.encode(), string_to_sign.encode(), hashlib.sha1).digest()).decode("ascii")
    headers = {
        "Authorization": f"account {ACCOUNT_KEY_ID}:{signature}",
        "Date": date,
        "Nonce": nonce,
        "Content-Type": "application/json",
    }
    if digest:
        headers["Content-MD5"] = digest
    return headers


def _account_post(client: httpx.Client, path: str, param: dict[str, Any]) -> dict[str, Any]:
    body = _account_body(param)
    try:
        response = client.post(
            f"{ACCOUNT_BASE}{path}",
            content=body,
            headers=_account_headers(path, body),
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"loomy: account request failed ({path}): {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("code") != "000000":
        detail = str(payload.get("desc") or payload.get("message") or "") if isinstance(payload, dict) else ""
        suffix = f": {detail}" if detail else ""
        raise LoginError(f"loomy: account request failed ({path}, HTTP {response.status_code}{suffix})")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _poll_once(client: httpx.Client, login_uuid: str, last: str) -> tuple[str, str]:
    params = {"uuid": login_uuid, "last": last, "_": str(int(time.time() * 1000))}
    try:
        response = client.get(WECHAT_POLL_URL, params=params, headers={"User-Agent": WECHAT_UA, "Referer": _auth_url("")}, timeout=40.0)
        body = response.text
    except httpx.HTTPError:
        return "error", "network"
    errcode = re.search(r"wx_errcode\s*=\s*(\d+)", body)
    code_match = re.search(r"wx_code\s*=\s*'([^']*)'", body)
    code = code_match.group(1) if code_match else ""
    value = errcode.group(1) if errcode else ""
    if value == "405" and code:
        return "confirmed", code
    if value in ("404", "405"):
        return "scanned", value
    if value == "403":
        return "cancelled", value
    if value == "402":
        return "expired", value
    return "waiting", value or "408"


def _image_data_url(client: httpx.Client, value: str) -> str:
    try:
        response = client.get(
            f"{WECHAT_QR_URL}/{quote(value, safe='')}",
            headers={"User-Agent": WECHAT_UA, "Referer": "https://open.weixin.qq.com/"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"loomy: WeChat QR download failed: {exc}") from exc
    content = response.content
    if response.status_code >= 400 or len(content) < 200:
        raise LoginError(f"loomy: WeChat QR response is unusable (HTTP {response.status_code})")
    if content.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif content.startswith(b"\x89PNG"):
        mime = "image/png"
    elif content.startswith(b"GIF"):
        mime = "image/gif"
    else:
        raise LoginError("loomy: WeChat QR response is not an image")
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def _credential(login: dict[str, Any], phone: str, nickname: str) -> dict[str, Any]:
    session = str(login.get("session") or "")
    userid = str(login.get("userid") or "")
    if not session or not userid:
        raise LoginError("loomy: login response missing session or userid")
    result: dict[str, Any] = {
        "access_token": session,
        "userid": userid,
        "user_id": userid,
        "expires_at": str(int((time.time() + SESSION_TTL_SECONDS) * 1000)),
    }
    final_phone = str(login.get("phone") or phone or "")
    if final_phone:
        result["phone"] = final_phone
    if nickname:
        result["nickname"] = nickname
    return result


def _page(qr_data_url: str) -> bytes:
    template = r'''<!doctype html><meta charset="utf-8"><title>Loomy login</title>
<style>body{font:14px system-ui,sans-serif;background:#f5f6f8;display:grid;place-items:center;margin:0;min-height:100vh}main{width:320px;background:#fff;padding:24px;border-radius:12px;box-shadow:0 8px 30px #0002}img{width:220px;height:220px;display:block;margin:12px auto;border:1px solid #ddd;border-radius:8px}#status{min-height:20px;color:#555}form{display:none;margin-top:12px}form.show{display:block}input{width:100%;box-sizing:border-box;margin:6px 0;padding:8px;border:1px solid #ccd;border-radius:6px}button{padding:8px 12px;border:0;border-radius:6px;background:#07c160;color:#fff;cursor:pointer}</style>
<main><h1>使用微信扫码登录 Loomy</h1><img alt="WeChat login QR" src="__QR__"><p id="status">等待扫码…</p><form id="phone"><label>首次使用需绑定手机号</label><input id="number" inputmode="numeric" maxlength="11" placeholder="手机号"><input id="code" inputmode="numeric" maxlength="6" placeholder="短信验证码"><button type="button" id="send">获取验证码</button><button type="button" id="verify">完成绑定并登录</button></form></main>
<script>
const status=document.getElementById('status'),form=document.getElementById('phone');
async function post(body){const r=await fetch('__COMPLETE__', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return r.json()}
async function poll(){try{const r=await fetch('__POLL__'),s=await r.json();if(s.status==='done'){status.textContent='登录成功，可以关闭此窗口了';return}if(s.status==='need_phone'){status.textContent='已扫码，请绑定手机号';form.className='show';return}if(s.status==='expired'||s.status==='cancelled'||s.status==='error'){status.textContent=s.message||'登录失败，请重试';return}if(s.status==='scanned')status.textContent='已扫码，请在手机上确认';setTimeout(poll,1200)}catch{setTimeout(poll,2000)}}
document.getElementById('send').onclick=async()=>{const phone=document.getElementById('number').value.replace(/\D/g,'');if(phone.length!==11)return status.textContent='请输入 11 位手机号';status.textContent='正在发送验证码…';const r=await post({action:'send_sms',phone});status.textContent=r.ok?'验证码已发送':(r.message||'发送失败')};
document.getElementById('verify').onclick=async()=>{const phone=document.getElementById('number').value.replace(/\D/g,''),code=document.getElementById('code').value;if(!phone||!code)return status.textContent='请输入手机号和验证码';status.textContent='正在完成绑定…';const r=await post({action:'verify_sms',phone,code});if(r.done)status.textContent='登录成功，可以关闭此窗口了';else status.textContent=r.message||'验证码错误'};
poll();
</script>'''
    return (template
            .replace("__QR__", qr_data_url)
            .replace("__COMPLETE__", COMPLETE_PATH)
            .replace("__POLL__", POLL_PATH)).encode("utf-8")



def start_loomy_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    """Serve one loopback WeChat page and return the resulting Loomy session."""
    state = secrets.token_hex(16)
    try:
        page = client.get(_auth_url(state), headers={"User-Agent": WECHAT_UA, "Referer": "https://open.weixin.qq.com/"}, timeout=30.0)
    except httpx.HTTPError as exc:
        raise LoginError(f"loomy: WeChat authorization page failed: {exc}") from exc
    value = _extract_uuid(page.text) if page.status_code < 400 else ""
    if not value:
        raise LoginError(f"loomy: WeChat authorization page has no QR uuid (HTTP {page.status_code})")
    qr_data_url = _image_data_url(client, value)

    answers: queue.Queue[dict[str, Any]] = queue.Queue()
    flow = {"last": "", "wechat_code": "", "rcode": "", "nickname": "", "msgid": "", "done": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == QR_PATH:
                body = _page(qr_data_url)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == POLL_PATH:
                self._poll()
                return
            self.send_error(404)

        def _poll(self) -> None:
            if flow.get("wechat_code"):
                self._json({"status": "done", "message": "登录成功，可以关闭此窗口了"})
                return
            status, raw = _poll_once(client, value, str(flow.get("last") or ""))
            if raw:
                flow["last"] = raw
            if status in ("expired", "cancelled", "error"):
                self._json({"status": status, "message": "二维码已失效或授权被取消，请重新发起登录"})
                return
            if status != "confirmed":
                self._json({"status": status})
                return
            code = raw
            flow["wechat_code"] = code
            try:
                auth = _account_post(client, "/login/thirdAccount/bind/auth", {"tcode": {"code": code}, "type": "wx"})
                flow["rcode"] = str(auth.get("rcode") or "")
                flow["nickname"] = str(auth.get("nickname") or "")
                if not flow["rcode"]:
                    raise LoginError("loomy: WeChat binding response missing rcode")
                if auth.get("bind") == 1:
                    login = _account_post(client, "/login/thirdAccount/bind/skip", {"rcode": flow["rcode"], "expire": SESSION_TTL_SECONDS})
                    self._json({"status": "done", "message": "登录成功，可以关闭此窗口了"})
                    answers.put(_credential(login, "", str(flow["nickname"])))
                else:
                    self._json({"status": "need_phone"})
            except LoginError as exc:
                self._json({"status": "error", "message": str(exc)})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != COMPLETE_PATH:
                self.send_error(404)
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
                payload = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                self._json({"ok": False, "message": "请求体不是合法 JSON"})
                return
            if not isinstance(payload, dict):
                self._json({"ok": False, "message": "请求体不是 JSON 对象"})
                return
            action = str(payload.get("action") or "")
            phone = str(payload.get("phone") or "")
            try:
                if action == "send_sms":
                    if not re.fullmatch(r"1\d{10}", phone):
                        raise LoginError("请输入有效的 11 位手机号")
                    sent = _account_post(client, "/login/thirdAccount/bind/sendMsg", {"rcode": flow["rcode"], "phone": phone, "ccode": "86", "expire": 300})
                    flow["msgid"] = str(sent.get("msgid") or "")
                    if not flow["msgid"]:
                        raise LoginError("loomy: SMS response missing msgid")
                    self._json({"ok": True})
                elif action == "verify_sms":
                    login = _account_post(client, "/login/thirdAccount/bind/checkCode", {"rcode": flow["rcode"], "mcode": str(payload.get("code") or ""), "msgid": flow["msgid"], "expire": SESSION_TTL_SECONDS})
                    self._json({"ok": True, "done": True})
                    answers.put(_credential(login, phone, str(flow["nickname"])))
                else:
                    self._json({"ok": False, "message": f"未知 action: {action}"})
            except LoginError as exc:
                self._json({"ok": False, "message": str(exc)})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:
        raise LoginError(f"loomy: cannot start the local login server: {exc}") from exc
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="hoshimi-loomy-login")
    thread.start()
    try:
        report(f"http://127.0.0.1:{server.server_address[1]}{QR_PATH}")
        try:
            credential = answers.get(timeout=LOGIN_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise LoginError("loomy: login timed out") from exc
        return credential
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
