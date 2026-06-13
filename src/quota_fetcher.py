import urllib.request
import urllib.error
import json
import ssl
import logging
from datetime import datetime, timezone

class ModelQuota:
    def __init__(self, label, model_id, remaining_fraction, reset_time_str):
        self.label = label
        self.model_id = model_id
        self.remaining_fraction = remaining_fraction  # float 0.0 - 1.0
        self.reset_time_str = reset_time_str           # ISO string
        self.percentage = int(remaining_fraction * 100) if remaining_fraction is not None else None
        self.time_until_reset_formatted = self.format_reset_time()

    def format_reset_time(self):
        if not self.reset_time_str:
            return "Unknown"
        try:
            # Parse ISO date string
            # Handle 'Z' suffix for Python older than 3.11
            clean_str = self.reset_time_str.replace("Z", "+00:00")
            reset_time = datetime.fromisoformat(clean_str)
            now = datetime.now(timezone.utc)
            
            diff = reset_time - now
            diff_seconds = diff.total_seconds()
            
            if diff_seconds <= 0:
                return "Ready"
                
            mins = int(diff_seconds // 60)
            if mins < 60:
                return f"{mins}m"
            else:
                hours = mins // 60
                rem_mins = mins % 60
                return f"{hours}h {rem_mins}m"
        except Exception as e:
            logging.debug(f"Error parsing reset time '{self.reset_time_str}': {e}")
            return "Unknown"

class CreditQuota:
    def __init__(self, available, monthly):
        self.available = available
        self.monthly = monthly
        self.used = monthly - available
        self.percentage = int((available / monthly) * 100) if monthly > 0 else 100

class QuotaSnapshot:
    def __init__(self, models, credits_info=None):
        self.timestamp = datetime.now()
        self.models = models  # List of ModelQuota
        self.credits = credits_info  # CreditQuota

def fetch_quota(proc_info):
    """
    Fetch quota snapshot from the running Antigravity process.
    proc_info: AntigravityProcessInfo object containing port and csrf_token.
    """
    if not proc_info or not proc_info.active_port:
        raise ValueError("Invalid process info or inactive API port.")
        
    url = f"https://127.0.0.1:{proc_info.active_port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": proc_info.csrf_token
    }
    body = json.dumps({
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "locale": "en"
        }
    }).encode("utf-8")
    
    # SSL setup (allow self-signed certificates)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            if response.status == 200:
                resp_data = response.read()
                data = json.loads(resp_data.decode("utf-8"))
                return parse_user_status_response(data)
            else:
                raise RuntimeError(f"API returned status {response.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with Language Server: {e}")

def parse_user_status_response(data):
    """Parse JSON response from GetUserStatus RPC."""
    user_status = data.get("userStatus", {})
    
    # Parse credits
    plan_status = user_status.get("planStatus", {})
    plan_info = plan_status.get("planInfo", {})
    available_credits = plan_status.get("availablePromptCredits")
    monthly_credits = plan_info.get("monthlyPromptCredits")
    
    credits_info = None
    if available_credits is not None and monthly_credits:
        credits_info = CreditQuota(int(available_credits), int(monthly_credits))
        
    # Parse models
    model_config_data = user_status.get("cascadeModelConfigData", {})
    client_configs = model_config_data.get("clientModelConfigs", [])
    
    models = []
    for cfg in client_configs:
        quota_info = cfg.get("quotaInfo")
        if not quota_info:
            continue
            
        label = cfg.get("label", "Unknown Model")
        model_id = cfg.get("modelOrAlias", {}).get("model", "unknown")
        
        remaining_fraction = quota_info.get("remainingFraction")
        # Ensure remaining_fraction is parsed as float
        if remaining_fraction is not None:
            remaining_fraction = float(remaining_fraction)
            
        reset_time_str = quota_info.get("resetTime")
        
        models.append(ModelQuota(label, model_id, remaining_fraction, reset_time_str))
        
    return QuotaSnapshot(models, credits_info)
