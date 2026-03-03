import httpx
import json
import os
import uuid
import re
from datetime import datetime, timezone

CONFIG_FILE = "config.json"
TOKENS_FILE = "tokens.json"
BASE_URL = "https://arena.ai"

# Markdown parser#
MARKPARSER = True  # When True filters out markdown code block delimiters (json, , etc.) or openai clients 

# ---------------- AUTO TOKEN SETTINGS ---------------- #
AUTO_TOKEN = True  # When True automatically loads and consumes tokens from tokens.json

# ---------------- CONFIG ---------------- #
def load_config():
    if not os.path.exists(CONFIG_FILE):
        default = {
            "auth_prod": "",
            "cf_clearance": "",
            "cf_bm": "",
            "eval_id": "",
            "modelAId": "",
            "OPENPARSER": True,
            "Tokenizer": True,
            "AUTO_TOKEN": AUTO_TOKEN  # GOOD IM DOING IT 
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

#  TOKEN MANAGEMENT #
def load_tokens():
    """Load tokens from tokens.json file"""
    if not os.path.exists(TOKENS_FILE):
        return {"tokens": [], "total_count": 0, "last_updated": ""}
    
    with open(TOKENS_FILE, "r") as f:
        return json.load(f)


def get_latest_token(version=None, max_age_seconds=110):
    """Get the most recent token from tokens.json based on timestamp.

    Argumentts:
        version: Filter by token version ("v3", "v2", or None for any).
        max_age_seconds: Reject tokens older than this many seconds (0 = no limit).
    """
    tokens_data = load_tokens()

    if not tokens_data.get("tokens"):
        return None, None

    tokens = tokens_data["tokens"]

    # Filter by version if specified WELL DONE 
    if version:
        tokens = [t for t in tokens if t.get("version", "v3") == version]

    if not tokens:
        return None, None

    # Sort tokens by timestamp_utc (latest comes first / early bird catches the worm)
    sorted_tokens = sorted(tokens, key=lambda x: x["timestamp_utc"], reverse=True)

    if not sorted_tokens:
        return None, None

    latest_token = sorted_tokens[0]

    # Check token freshness/ isnt it spoiled?
    if max_age_seconds > 0:
        try:
            token_time = datetime.fromisoformat(
                latest_token["timestamp_utc"].rstrip("Z")
            ).replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - token_time).total_seconds()
            if age > max_age_seconds:
                return None, None
        except (KeyError, ValueError):
            pass  # If timestamp is missing/malformed, still return the token

    return latest_token["token"], latest_token

def consume_token(token_to_remove):
    """Remove a specific token from tokens.json"""
    tokens_data = load_tokens()
    
    # Filter out the token to remove
    original_count = len(tokens_data.get("tokens", []))
    tokens_data["tokens"] = [
        t for t in tokens_data.get("tokens", []) 
        if t["token"] != token_to_remove
    ]
    
    # Update total_count
    tokens_data["total_count"] = len(tokens_data["tokens"])
    tokens_data["last_updated"] = datetime.utcnow().isoformat() + "Z"
    
    
    removed_count = original_count - len(tokens_data["tokens"])
    return removed_count > 0

def should_filter_content(content):
    """
    Filter out unwanted content based on parser settings.
    Returns True if content should be skipped.
    """
    # Always filter heartbeat messages BECAUSE HEARTBEAT CAN MESS OUTPUT DATA
    if content and "[{" in content and "heartbeat" in content:
        return True

    # Filter markdown code blocks if MARKPARSER is enabled
    if MARKPARSER:
        # Filter opening code blocks: ```json, ```python, ```, etc.
        if re.match(r'^```\w*\n?$', content.strip()):
            return True
        # Filter closing code blocks: ```
        if content.strip() == '```':
            return True

    return False

def main():
    # Load config
    cfg = load_config()

    # ---------------- INPUT PROMPTS ---------------- #
    if not cfg["auth_prod"]:
        cfg["auth_prod"] = input("Enter arena-auth-prod-v1 cookie: ").strip()

    if not cfg["cf_clearance"]:
        cfg["cf_clearance"] = input("Enter cf_clearance cookie: ").strip()

    if not cfg["cf_bm"]:
        cfg["cf_bm"] = input("Enter __cf_bm cookie: ").strip()

    if not cfg["eval_id"]:
        cfg["eval_id"] = input("Enter Evaluation ID: ").strip()

    if not cfg["modelAId"]:
        cfg["modelAId"] = input("Enter modelAId: ").strip()

    # Save config
    save_config(cfg)

    # ---------------- AUTO TOKEN HANDLING ---------------- #
    recaptcha_token = None
    used_token_data = None

    if cfg.get("AUTO_TOKEN", AUTO_TOKEN):
        print("\n🔑 AUTO_TOKEN enabled: Loading latest token from tokens.json...")
        recaptcha_token, used_token_data = get_latest_token()

        if recaptcha_token:
            print(f"✅ Found token from {used_token_data.get('timestamp_local', 'unknown')}")
            print(f"📝 Token preview: {used_token_data.get('token_preview', 'N/A')}")
        else:
            print("⚠️  No tokens found in tokens.json. Falling back to manual input.")
            recaptcha_token = input("Enter reCAPTCHA token (one-time): ").strip()
    else:
        recaptcha_token = input("Enter reCAPTCHA token (one-time): ").strip()

    prompt_text = input("Enter your prompt: ").strip()

    # ---------------- ARENA REQUIRED IDS ---------------- #
    user_message_id = str(uuid.uuid4())
    model_message_id = str(uuid.uuid4())

    # ---------------- HEADERS ---------------- #
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/c/{cfg['eval_id']}",
        "user-agent": "Mozilla/5.0",
    }

    cookies = {
        "arena-auth-prod-v1": cfg["auth_prod"],
        "cf_clearance": cfg["cf_clearance"],
        "__cf_bm": cfg["cf_bm"]
    }

    # ---------------- CORRECT PAYLOAD ---------------- #
    payload = {
        "id": cfg["eval_id"],
        "modelAId": cfg["modelAId"],
        "userMessageId": user_message_id,
        "modelAMessageId": model_message_id,
        "userMessage": {
            "content": prompt_text,
            "experimental_attachments": [],
            "metadata": {}
        },
        "modality": "chat",
        "recaptchaV3Token": recaptcha_token
    }

    url = f"{BASE_URL}/nextjs-api/stream/post-to-evaluation/{cfg['eval_id']}"

    print("\nConnecting...\n")

    try:
        with httpx.Client(http2=True, timeout=None) as client:
            with client.stream("POST", url, headers=headers, cookies=cookies, json=payload) as response:
                print("Status Code:", response.status_code)

                if response.status_code != 200:
                    print("Server error.")
                    print(response.text)
                    exit()

                # 🔥 TOKENIZER AUTO UPDATE TO STAY UPDATED TO WORLD
                if cfg.get("Tokenizer"):
                    new_token = response.cookies.get("arena-auth-prod-v1")
                    if new_token:
                        cfg["auth_prod"] = new_token
                        save_config(cfg)
                        print("✅ arena-auth-prod-v1 updated")

                print("\n--- Streaming ---\n")

                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue

                    # Match ag:, a0:, a1:, etc. These are Lmarena sse chunks
                    match = re.match(r'^([a-z0-9]+):(.*)', raw_line)
                    if not match:
                        continue

                    prefix = match.group(1)
                    data = match.group(2).strip()

                    # Finish event
                    if prefix == "ad":
                        if cfg["OPENPARSER"]:
                            print('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
                            print("data: [DONE]")
                        print("\n\n--- Completed ---")
                        break

                    # Decode JSON string safely
                    if data.startswith('"') and data.endswith('"'):
                        try:
                            data = json.loads(data)
                        except:
                            pass

                    # ---------------- CONTENT FILTERING ---------------- #
                    # Skip filtered content (heartbeats, markdown blocks)
                    if should_filter_content(data):
                        continue

                    # ---------------- OPENPARSER ---------------- #
                    if cfg["OPENPARSER"]:
                        openai_chunk = {
                            "choices": [
                                {
                                    "delta": {"content": data},
                                    "index": 0,
                                    "finish_reason": None
                                }
                            ]
                        }
                        print("data:", json.dumps(openai_chunk))
                    else:
                        print(data, end="", flush=True)

    except Exception as e:
        print(f"\n❌ Error during request: {e}")

    finally:
        # ---------------- CONSUME USED TOKEN ---------------- #
        if cfg.get("AUTO_TOKEN", AUTO_TOKEN) and used_token_data and recaptcha_token:
            print("\n" + "="*50)
            print("♻️  Cleaning up: Consuming used token...")
            if consume_token(recaptcha_token):
                print(f"✅ Successfully removed used token from {TOKENS_FILE}")
                print(f"📊 Remaining tokens: {load_tokens().get('total_count', 0)}")
            else:
                print(f"⚠️  Token not found in {TOKENS_FILE} (may have been already consumed)")
            print("="*50)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
