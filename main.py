"""
THIS IS :
-----------------------
A modular, httpx-based client for interacting with the Arena API.
Supports Chat, Search, Reasoning, Image Generation, and Image Editing (uploading images).

Usage:
    python arena_client.py
    
To use Image Edit mode, ensure your config file has:
    "image": true,
    "image_edit": true
"""

import httpx
import json
import os
import uuid
import re
import base64
from datetime import datetime

# Import utilities from your modula script i didnt had time to copy paste functions LOL
from modula import (
    load_config,
    save_config,
    load_tokens,
    get_latest_token,
    consume_token,
    should_filter_content,
    CONFIG_FILE,
    TOKENS_FILE,
    BASE_URL,
    AUTO_TOKEN,
)

# ---------------- DEFAULT MODEL IDS ---------------- #
DEFAULT_SEARCH_MODEL = "019c6f55-308b-71ac-95af-f023a48253cf"
DEFAULT_THINK_MODEL = "019c2f86-74db-7cc3-baa5-6891bebb5999"
DEFAULT_IMG_MODEL = "019abc10-e78d-7932-b725-7f1563ed8a12"

# ---------------- reCAPTCHA CONSTANTS ---------------- #
RECAPTCHA_V2_SITEKEY = "6Ld7ePYrAAAAAB34ovoFoDau1fqCJ6IyOjFEQaMn"
RECAPTCHA_ACTION = "chat_submit"
MAX_RECAPTCHA_ATTEMPTS = 2


# ---------------- CONFIG EXTENSION ---------------- #
def ensure_extended_config(cfg):
    """Add new config fields with defaults if they don't already exist."""
    # Ask about v2_auth ONLY if not present
    if "v2_auth" not in cfg:
        print("Are you using Lmarena logged in?")
        print("1. Yes")
        print("2. No")
        choice = input("Select option (1/2): ").strip()

        cfg["v2_auth"] = True if choice == "1" else False
        save_config(cfg)

    defaults = {
        "search": False,
        "reasoning": False,
        "image": False,
        "image_edit": False,  # MUST be true alongside "image": true for edit mode
        "searchmodel": DEFAULT_SEARCH_MODEL,
        "thinkmodel": DEFAULT_THINK_MODEL,
        "imgmodel": DEFAULT_IMG_MODEL,
    }
    changed = False
    for key, default_value in defaults.items():
        if key not in cfg:
            cfg[key] = default_value
            changed = True
    if changed:
        save_config(cfg)
    return cfg


# ---------------- MODE DETECTION ---------------- #
def detect_mode(cfg):
    """Return 'image_edit', 'image', 'search', 'reasoning', or 'chat' based on config flags."""
    if cfg.get("image", False):
        if cfg.get("image_edit", False):
            return "image_edit"
        return "image"
    if cfg.get("search", False):
        return "search"
    if cfg.get("reasoning", False):
        return "reasoning"
    return "chat"


def resolve_model_id(cfg, mode):
    """Return the appropriate model ID for the current mode."""
    if mode in ["image", "image_edit"]:
        return cfg.get("imgmodel") or DEFAULT_IMG_MODEL
    elif mode == "search":
        return cfg.get("searchmodel") or DEFAULT_SEARCH_MODEL
    elif mode == "reasoning":
        return cfg.get("thinkmodel") or DEFAULT_THINK_MODEL
    else:
        return cfg.get("modelAId")


# ---------------- HEADER BUILDERS ---------------- #
def build_base_headers(cfg):
    """Standard base headers applied across modes. change this accordingly for more stealth"""
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/c/{cfg['eval_id']}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }


def build_chat_headers(cfg):
    """Headers for normal chat and reasoning modes."""
    headers = build_base_headers(cfg)
    headers["content-type"] = "application/json"
    return headers


def build_search_headers(cfg):
    """Headers for search and image modes (text/plain content-type)."""
    headers = build_base_headers(cfg)
    headers.update({
        "content-type": "text/plain;charset=UTF-8",
        "priority": "u=1, i",
        "sec-ch-ua": '"Chromium";v="145", "Not:A-Brand";v="99"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Linux"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    return headers


# ---------------- IMAGE EDIT / UPLOAD HANDLERS ---------------- #
def get_image_data():
    """Prompts user for Base64 or File Path, returns (image_bytes, mime_type)."""
    print("\n[Image Edit Mode Active]")
    print("1. Select /path/to/image")
    choice = input("Choice (1): ").strip()

    image_bytes = None
    mime_type = "image/png"  # Default

    if choice == "2":
        b64_string = input("Paste your Base64 string: ").strip()
        # Parse potential data URI scheme (e.g., data:image/jpeg;base64,...)
        if b64_string.startswith("data:"):
            header, b64_string = b64_string.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1]
        
        image_bytes = base64.b64decode(b64_string)

    elif choice == "1":
        file_path = input("Enter full path to image: ").strip()
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Image not found at {file_path}")
        
        # Determine mime type from extension
        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif"
        }
        mime_type = mime_map.get(ext, "image/png")

        with open(file_path, "rb") as f:
            image_bytes = f.read()
    else:
        raise ValueError("Invalid choice for image input.")

    return image_bytes, mime_type


def upload_image_handshake(client, cfg, image_bytes, mime_type):
    """Executes the Arena 2-step image upload handshake. Returns the signed URL."""
    print("⏳ Reserving upload slot...")
    reserve_url = f"{BASE_URL}/c/{cfg['eval_id']}"
    
    headers = build_base_headers(cfg)
    headers["next-action"] = "7012303914af71fce235a732cde90253f7e2986f2b"
    headers["content-type"] = "application/json"
    
    # 1. Post to reserve slot
    payload = ["image.png", mime_type]
    res = client.post(reserve_url, headers=headers, json=payload)
    res.raise_for_status()
    
    # Extract Cloudflare signed URL from response text
    match = re.search(r'https:\/\/[^\s"\'\\]+\.cloudflarestorage\.com[^\s"\'\\]+', res.text)
    if not match:
        raise Exception("Failed to extract signed URL from handshake response.")
    
    signed_url = match.group(0).replace('\\u0026', '&')
    
    # 2. Upload image payload
    print("⏳ Uploading image data...")
    upload_res = client.put(signed_url, headers={"Content-Type": mime_type}, content=image_bytes)
    upload_res.raise_for_status()
    print("✅ Image Uploaded successfully.")
    
    return signed_url


# ---------------- reCAPTCHA HELPERS ---------------- #
def _is_recaptcha_validation_failed(status_code, response_text):
    """Detect a 403 'recaptcha validation failed' response (ported from main.py)."""
    if status_code != 403:
        return False
    if not isinstance(response_text, str) or not response_text:
        return False
    try:
        body = json.loads(response_text)
    except Exception:
        return False
    return isinstance(body, dict) and body.get("error") == "recaptcha validation failed"


# ---------------- PAYLOAD BUILDER ---------------- #
def build_payload(cfg, mode, model_id, prompt_text, recaptcha_token, attachment_url=None, mime_type=None, recaptcha_v2_token=None):
    """Build the request payload. Injects experimental_attachments if an image was uploaded."""
    user_message_id = str(uuid.uuid4())
    model_message_id = str(uuid.uuid4())

    if mode in ["image", "image_edit"]:
        modality = "image"
    elif mode == "search":
        modality = "search"
    else:
        modality = "chat"

    attachments = []
    if attachment_url and mime_type:
        attachments.append({
            "name": "image.png",
            "contentType": mime_type,
            "url": attachment_url
        })

    payload = {
        "id": cfg["eval_id"],
        "modelAId": model_id,
        "userMessageId": user_message_id,
        "modelAMessageId": model_message_id,
        "userMessage": {
            "content": prompt_text,
            "experimental_attachments": attachments,
            "metadata": {},
        },
        "modality": modality,
        "recaptchaV3Token": recaptcha_token,
    }

    # When using a v2 token, swap out the v3 field (matches main.py behavior)
    if recaptcha_v2_token:
        payload["recaptchaV2Token"] = recaptcha_v2_token
        payload.pop("recaptchaV3Token", None)

    return payload


# ---------------- OPENAI-COMPATIBLE FORMATTERS ---------------- #
def format_content_chunk(token):
    return f"data: {json.dumps({'choices': [{'delta': {'content': token}, 'index': 0, 'finish_reason': None}]})}"

def format_reasoning_chunk(token):
    return f"data: {json.dumps({'choices': [{'delta': {'reasoning_content': token}, 'index': 0, 'finish_reason': None}]})}"

def format_citation_chunk(citation_data):
    return f"data: {json.dumps({'choices': [{'delta': {'citations': [citation_data]}, 'index': 0, 'finish_reason': None}]})}"

def format_image_chunk(image_url, mime_type="image/png"):
    return f"data: {json.dumps({'data': [{'url': image_url, 'revised_prompt': None}]})}"

def format_finish():
    return 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\ndata: [DONE]'


# ---------------- CITATION ACCUMULATOR ---------------- #
class CitationAccumulator:
    def __init__(self):
        self._buffer = ""

    def feed(self, raw_data):
        try:
            outer = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            return None
        if outer.get("toolCallId") != "citation-source":
            return None

        self._buffer += outer.get("argsTextDelta", "")
        try:
            citation = json.loads(self._buffer)
            self._buffer = ""
            return citation
        except json.JSONDecodeError:
            return None


def _decode_data(data):
    if data.startswith('"') and data.endswith('"'):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            pass
    return data


# ---------------- STREAM PROCESSOR ---------------- #
def process_stream(response_lines, cfg, mode):
    openparser = cfg.get("OPENPARSER", False)
    citation_acc = CitationAccumulator() if mode == "search" else None

    for raw_line in response_lines:
        if not raw_line:
            continue

        match = re.match(r'^([a-z0-9]+):(.*)', raw_line)
        if not match:
            continue

        prefix, data = match.group(1), match.group(2).strip()

        if prefix == "ad":
            if openparser:
                print(format_finish())
            print("\n\n--- Completed ---")
            break

        if prefix == "a2":
            if mode in ["image", "image_edit"]:
                try:
                    heartbeat_data = json.loads(data)
                    if isinstance(heartbeat_data, list):
                        for item in heartbeat_data:
                            if isinstance(item, dict) and item.get("type") == "image":
                                image_url = item.get("image")
                                mime_type = item.get("mimeType", "image/png")
                                if openparser:
                                    print(format_image_chunk(image_url, mime_type))
                                else:
                                    print(f"\n🖼️  Generated Image URL:\n{image_url}\n")
                                    print(f"📄 MIME Type: {mime_type}")
                except (json.JSONDecodeError, TypeError):
                    pass
            continue

        if prefix == "ac":
            if mode == "search" and citation_acc is not None:
                citation = citation_acc.feed(data)
                if citation is not None:
                    if openparser:
                        print(format_citation_chunk(citation))
                    else:
                        c_list = citation if isinstance(citation, list) else [citation]
                        for c in c_list:
                            print(f"\n[Citation: {c.get('title', '')} - {c.get('url', '')}]")
            continue

        if prefix == "ag":
            if mode == "reasoning":
                token = _decode_data(data)
                if should_filter_content(token): continue
                if openparser:
                    print(format_reasoning_chunk(token))
                else:
                    print(f"[think] {token}", end="", flush=True)
            continue

        if prefix == "a0":
            token = _decode_data(data)
            if should_filter_content(token): continue
            if openparser:
                print(format_content_chunk(token))
            else:
                print(token, end="", flush=True)
            continue


# ---------------- REQUEST EXECUTOR ---------------- #
def execute_request(cfg, mode, model_id, prompt_text, recaptcha_token):
    auth_cookie_key = "arena-auth-prod-v1.0" if cfg.get("v2_auth") else "arena-auth-prod-v1"
    cookies = {
        auth_cookie_key: cfg["auth_prod"],
        "cf_clearance": cfg["cf_clearance"],
        "__cf_bm": cfg["cf_bm"],
    }

    if cfg.get("v2_auth"):
        cookies["domain_migration_completed"] = "true"
        cookies["arena-auth-prod-v1.1"] = cfg.get("auth_prod_v2", "")

    url = f"{BASE_URL}/nextjs-api/stream/post-to-evaluation/{cfg['eval_id']}"
    headers = build_search_headers(cfg) if mode in ["search", "image", "image_edit"] else build_chat_headers(cfg)

    # Add reCAPTCHA headers (matches main.py behavior)
    if recaptcha_token:
        headers["X-Recaptcha-Token"] = recaptcha_token
        headers["X-Recaptcha-Action"] = RECAPTCHA_ACTION

    attachment_url = None
    mime_type = None

    print(f"\nConnecting in {mode.upper()} mode...\n")

    # Use a persistent client session to share cookies for the upload handshake and streaming
    with httpx.Client(http2=True, timeout=None, cookies=cookies) as client:

        # If in image_edit mode, trigger the prompt & upload first
        if mode == "image_edit":
            image_bytes, mime_type = get_image_data()
            attachment_url = upload_image_handshake(client, cfg, image_bytes, mime_type)

        payload = build_payload(cfg, mode, model_id, prompt_text, recaptcha_token, attachment_url, mime_type)

        for attempt in range(MAX_RECAPTCHA_ATTEMPTS):
            if mode in ["search", "image", "image_edit"]:
                body_str = json.dumps(payload)
                stream_ctx = client.stream("POST", url, headers=headers, content=body_str.encode("utf-8"))
            else:
                stream_ctx = client.stream("POST", url, headers=headers, json=payload)

            with stream_ctx as response:
                # Read full body for non-200 to check recaptcha failure
                if response.status_code != 200:
                    error_body = ""
                    for chunk in response.iter_bytes():
                        error_body += chunk.decode("utf-8", errors="replace")

                    # Check for reCAPTCHA validation failure — try v2 fallback
                    if _is_recaptcha_validation_failed(response.status_code, error_body):
                        print(f"⚠️  reCAPTCHA v3 validation failed (attempt {attempt + 1}/{MAX_RECAPTCHA_ATTEMPTS})")

                        if attempt < MAX_RECAPTCHA_ATTEMPTS - 1:
                            # Try to get a v2 token from the harvester
                            v2_token, v2_data = get_latest_token(version="v2", max_age_seconds=110)
                            if v2_token:
                                print(f"🔄 Retrying with v2 token...")
                                payload["recaptchaV2Token"] = v2_token
                                payload.pop("recaptchaV3Token", None)
                                # Update headers — remove v3 token header for v2 attempt
                                headers.pop("X-Recaptcha-Token", None)
                                headers.pop("X-Recaptcha-Action", None)
                                # Consume the v2 token so it's not reused
                                consume_token(v2_token)
                                continue
                            else:
                                # No v2 token available — try a fresh v3 token
                                fresh_v3, _ = get_latest_token(version="v3", max_age_seconds=110)
                                if fresh_v3 and fresh_v3 != recaptcha_token:
                                    print(f"🔄 Retrying with fresh v3 token...")
                                    payload["recaptchaV3Token"] = fresh_v3
                                    payload.pop("recaptchaV2Token", None)
                                    headers["X-Recaptcha-Token"] = fresh_v3
                                    headers["X-Recaptcha-Action"] = RECAPTCHA_ACTION
                                    continue
                                print("❌ No v2 tokens available and no fresh v3 token. Run the v2 harvester in your browser.")
                        else:
                            print("❌ All reCAPTCHA attempts exhausted.")

                        print(f"Server error. Status Code: {response.status_code}")
                        print(error_body)
                        return

                    # Non-recaptcha error
                    print(f"Server error. Status Code: {response.status_code}")
                    print(error_body)
                    return

                # Success (200) — stream the response
                if cfg.get("Tokenizer"):
                    
                    # Pick correct cookie name based on v2_auth
                    cookie_name = "arena-auth-prod-v1.0" if cfg.get("v2_auth") else "arena-auth-prod-v1"

                    new_token = response.cookies.get(cookie_name)
                    if new_token:
                        cfg["auth_prod"] = new_token
                        save_config(cfg)
                        print(f"✅ {cookie_name} cookie refreshed")

                print(f"\n--- Streaming ({mode}) ---\n")
                process_stream(response.iter_lines(), cfg, mode)
                return  # Success — exit retry loop


# ---------------- MAIN ENTRYPOINT ---------------- #
def main():
    cfg = load_config()
    cfg = ensure_extended_config(cfg)

    # Decide cookie label based on v2_auth
    auth_cookie_label = "arena-auth-prod-v1.0" if cfg.get("v2_auth") else "arena-auth-prod-v1"
    # First-time / Base config checking
    missing_config = False
    if not cfg.get("auth_prod"):
        cfg["auth_prod"] = input(f"Enter {auth_cookie_label} cookie: ").strip()
        missing_config = True
    if not cfg.get("cf_clearance"):
        cfg["cf_clearance"] = input("Enter cf_clearance cookie: ").strip()
        missing_config = True
    if not cfg.get("cf_bm"):
        cfg["cf_bm"] = input("Enter __cf_bm cookie: ").strip()
        missing_config = True
    if not cfg.get("eval_id"):
        cfg["eval_id"] = input("Enter Evaluation ID: ").strip()
        missing_config = True
    if cfg.get("v2_auth") and not cfg.get("auth_prod_v2"):
        cfg["auth_prod_v2"] = input("Enter arena-auth-prod-v1.1 cookie: ").strip()
        missing_config = True
    if not cfg.get("modelAId"):
        cfg["modelAId"] = input("Enter  model_ID: ").strip()
        missing_config = True

    mode = detect_mode(cfg)
    model_id = resolve_model_id(cfg, mode)


    if missing_config:
        save_config(cfg)
        print("\n✅ Cookies and base settings saved to config.")

    print(f"\nMode: {mode.upper()} | Model ID: {model_id}")

    # For each run: ONLY ask for tokens, prompt, and optional image
    recaptcha_token = None
    used_token_data = None

    if cfg.get("AUTO_TOKEN", AUTO_TOKEN):
        recaptcha_token, used_token_data = get_latest_token(version="v3", max_age_seconds=110)
        if not recaptcha_token:
            # Fallback: try any token regardless of version/age
            recaptcha_token, used_token_data = get_latest_token(version=None, max_age_seconds=0)
        if not recaptcha_token:
            print("⚠️ No fresh tokens found in tokens.json.")
            recaptcha_token = input("Enter fresh reCAPTCHA v3 token: ").strip()
    else:
        recaptcha_token = input("Enter fresh reCAPTCHA v3 token: ").strip()

    prompt_text = input("Enter your prompt: ").strip()

    try:
        execute_request(cfg, mode, model_id, prompt_text, recaptcha_token)
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        if cfg.get("AUTO_TOKEN", AUTO_TOKEN) and used_token_data and recaptcha_token:
            consume_token(recaptcha_token)

if __name__ == "__main__":
    main()
