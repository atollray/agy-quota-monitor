import subprocess
import os
import json
import re
import urllib.request
import urllib.error
import ssl
import logging
import time

# Windows-specific flags to prevent console flashing
SUBPROCESS_CREATIONFLAGS = 0
if os.name == 'nt':
    SUBPROCESS_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW

# Process names we look for
TARGET_PROCESSES = ["language_server.exe", "language_server_windows_x64.exe"]

# Compile Regex patterns
CSRF_PATTERN = re.compile(r'--csrf_token[=\s]+([a-f0-9\-]+)', re.IGNORECASE)
PORT_PATTERN = re.compile(r'--extension_server_port[=\s]+(\d+)', re.IGNORECASE)

class AntigravityProcessInfo:
    def __init__(self, pid, name, cmdline, csrf_token, extension_port=None):
        self.pid = pid
        self.name = name
        self.cmdline = cmdline
        self.csrf_token = csrf_token
        self.extension_port = extension_port
        self.active_port = None

    def mask_token(self):
        if len(self.csrf_token) > 8:
            return f"{self.csrf_token[:8]}..."
        return "..."

    def __str__(self):
        return f"PID {self.pid} ({self.name}) | Token: {self.mask_token()} | Port: {self.active_port or 'Unknown'}"

def get_process_list_powershell():
    """Get list of candidate processes using PowerShell."""
    filter_expr = " or ".join([f"name='{p}'" for p in TARGET_PROCESSES])
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"Get-CimInstance Win32_Process -Filter \"{filter_expr}\" | "
        "Select-Object ProcessId, Name, CommandLine | ConvertTo-Json"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="ignore", creationflags=SUBPROCESS_CREATIONFLAGS)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            # Convert single object to list if only one process returned
            if isinstance(data, dict):
                return [data]
            elif isinstance(data, list):
                return data
    except Exception as e:
        logging.warning(f"PowerShell process retrieval failed: {e}")
    return []

def get_process_list_wmic():
    """Fallback method using WMIC to find candidate processes."""
    cmd = ["wmic", "process", "where", 
           "name='language_server.exe' or name='language_server_windows_x64.exe'", 
           "get", "ProcessId,Name,CommandLine", "/format:list"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="ignore", creationflags=SUBPROCESS_CREATIONFLAGS)
        if res.returncode == 0:
            processes = []
            blocks = res.stdout.split("\n\n")
            for block in blocks:
                if not block.strip():
                    continue
                pid_match = re.search(r'ProcessId=(\d+)', block)
                name_match = re.search(r'Name=([^\n\r]+)', block)
                cmd_match = re.search(r'CommandLine=([^\n\r]+)', block)
                if pid_match and name_match:
                    processes.append({
                        "ProcessId": int(pid_match.group(1)),
                        "Name": name_match.group(1).strip(),
                        "CommandLine": cmd_match.group(1).strip() if cmd_match else ""
                    })
            return processes
    except Exception as e:
        logging.warning(f"WMIC process retrieval failed: {e}")
    return []

def is_antigravity_process(cmdline):
    """Check if process is associated with Antigravity."""
    if not cmdline:
        return False
    lower_cmd = cmdline.lower()
    
    # Must contain antigravity or antigravity-ide in --app_data_dir or path
    if "antigravity" in lower_cmd:
        return True
    return False

def get_candidate_processes():
    """Retrieve and filter Antigravity processes."""
    raw_list = get_process_list_powershell()
    if not raw_list:
        raw_list = get_process_list_wmic()
        
    candidates = []
    for proc in raw_list:
        pid = proc.get("ProcessId")
        name = proc.get("Name")
        cmdline = proc.get("CommandLine") or ""
        
        if not pid or not is_antigravity_process(cmdline):
            continue
            
        csrf_match = CSRF_PATTERN.search(cmdline)
        if not csrf_match:
            # No CSRF token means we can't authenticate to its API
            continue
            
        csrf_token = csrf_match.group(1)
        port_match = PORT_PATTERN.search(cmdline)
        ext_port = int(port_match.group(1)) if port_match else None
        
        candidates.append(AntigravityProcessInfo(pid, name, cmdline, csrf_token, ext_port))
        
    return candidates

def get_listening_ports_powershell(pid):
    """Get listening ports of a PID using PowerShell."""
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"Get-NetTCPConnection -OwningProcess {pid} -State Listen | "
        "Select-Object -ExpandProperty LocalPort | ConvertTo-Json"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="ignore", creationflags=SUBPROCESS_CREATIONFLAGS)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, int):
                return [data]
            elif isinstance(data, list):
                return list(set(data))
    except Exception as e:
        logging.debug(f"PowerShell port query failed for PID {pid}: {e}")
    return []

def get_listening_ports_netstat(pid):
    """Fallback to query listening ports using netstat."""
    cmd = ["netstat", "-ano"]
    ports = []
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="ignore", creationflags=SUBPROCESS_CREATIONFLAGS)
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            for line in lines:
                parts = line.strip().split()
                # Typical TCP line: TCP 127.0.0.1:56789 0.0.0.0:0 LISTENING <PID>
                if len(parts) >= 5 and parts[0] == "TCP" and parts[-1] == str(pid):
                    state = parts[3] if len(parts) == 5 else parts[3]
                    # Check if LISTENING
                    if "LISTENING" in line.upper():
                        addr = parts[1]
                        # Extract port from IP:Port
                        port_match = re.search(r':(\d+)$', addr)
                        if port_match:
                            ports.append(int(port_match.group(1)))
            return list(set(ports))
    except Exception as e:
        logging.debug(f"Netstat port query failed for PID {pid}: {e}")
    return []

def get_listening_ports(pid):
    """Get unique listening ports for a PID."""
    ports = get_listening_ports_powershell(pid)
    if not ports:
        ports = get_listening_ports_netstat(pid)
    return sorted(ports)

def verify_api_port(port, csrf_token):
    """Send health check request to determine if this is the active API port."""
    url = f"https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUnleashData"
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": csrf_token
    }
    body = json.dumps({"wrapper_data": {}}).encode("utf-8")
    
    # Self-signed certificate setup
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=2) as response:
            if response.status == 200:
                resp_data = response.read()
                # Check if response is valid JSON
                json.loads(resp_data.decode("utf-8"))
                return True
    except urllib.error.HTTPError as e:
        # Connect RPC protocol might return non-200, but let's check code
        logging.debug(f"Health check to port {port} HTTP Error: {e.code}")
    except Exception as e:
        logging.debug(f"Health check to port {port} failed: {e}")
    return False

def discover_active_processes():
    """
    Search and verify all running Antigravity processes.
    Returns a list of validated AntigravityProcessInfo objects.
    """
    candidates = get_candidate_processes()
    logging.info(f"Found {len(candidates)} candidate Antigravity processes.")
    
    valid_processes = []
    for proc in candidates:
        logging.info(f"Checking PID {proc.pid} ({proc.name})...")
        
        # Test command line extension port if present
        if proc.extension_port:
            logging.info(f"Testing extension port {proc.extension_port} from command line...")
            if verify_api_port(proc.extension_port, proc.csrf_token):
                proc.active_port = proc.extension_port
                valid_processes.append(proc)
                logging.info(f"PID {proc.pid} successfully verified on command-line port {proc.extension_port}.")
                continue
                
        # Fallback: scan all listening ports owned by the process
        ports = get_listening_ports(proc.pid)
        logging.info(f"PID {proc.pid} listening ports: {ports}")
        
        port_found = False
        for port in ports:
            # Skip testing command line port again
            if port == proc.extension_port:
                continue
            logging.info(f"Testing port {port}...")
            if verify_api_port(port, proc.csrf_token):
                proc.active_port = port
                valid_processes.append(proc)
                port_found = True
                logging.info(f"PID {proc.pid} successfully verified on port {port}.")
                break
                
        if not port_found and not proc.active_port:
            logging.warning(f"Could not verify any listening ports for PID {proc.pid}.")
            
    return valid_processes
