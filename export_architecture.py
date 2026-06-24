import os
import re
import sys

MERMAID_DIAGRAMS = """# UC System Architecture & Structural Flows

## 1. High-Level Architecture Diagram
```mermaid
graph TD
    UI["Tkinter GUI: validator_pro_v2.py"]
    EDF["Expected-Utility Scheduler: scheduler.py"]
    SI["Session Isolation Manager: session_isolation.py"]
    BF["Browser Factory: browser_factory.py"]
    HE["Heuristic CSS Engine: heuristics.py"]
    TDA["Topological DOM Matcher: tda.py"]
    CR["Zero-Trust Cryptography: crypto.py"]
    EN["Thermodynamic Entropy Monitor: entropy.py"]
    CDP["Chrome DevTools Protocol - CDP"]
    IP["Ingestion Pipeline: validator_pro_v2.py"]

    UI -->|1. Processes imports via| IP
    UI -->|2. Schedules validation tasks| EDF
    EDF -->|3. Executes tasks under LogNormal latency| SI
    SI -->|4. Configures temp profiles & rotates HWID| BF
    BF -->|5. Launches Undetected Chrome| CDP
    CDP -->|6. Validates page states| HE
    HE -->|7. Fallback on obfuscation| TDA
    TDA -->|8. Tree Edit Distance matching| CDP
    SI -->|9. Encrypts registry & settings| CR
    BF -->|10. Verifies fingerprint distribution| EN
```

## 2. Topological Element Selection Sequence (Epics 2, 3 & 8)
```mermaid
sequenceDiagram
    autonumber
    participant V as validator_pro_v2.py
    participant H as heuristics.py
    participant T as tda.py (Zhang-Shasha TED)
    participant C as Chrome Browser (CDP)

    V->>C: Look up element via Config Selector (Tier 1)
    alt Selector Found
        C-->>V: Return element
    else Selector Missing / Obfuscated (Timeout)
        V->>H: Query common fallback patterns (Tier 2)
        H->>C: Scan 80+ CSS selectors & placeholders
        alt Heuristics Match
            C-->>V: Return element & update UI config
        else Heuristics Fail
            V->>T: Retrieve page DOM Tree (Tier 3)
            T->>C: Extract current accessibility / DOM tree
            C-->>T: Return target DOM Node tree
            T->>T: Stage 1: Prune DOM Tree (removes wrappers, scripts, styles)
            T->>T: Stage 2: Compute Subtree SimHash (filter candidates via Hamming <= 25)
            T->>T: Stage 4: Compute ZSS Tree Edit Distance on candidate pairs (Approximate TED if >80 nodes)
            T->>T: Verify Lipschitz Continuity Constraint (L2C2) on coordinates (d_spatial <= L * d_dom)
            T-->>V: Return closest structural match & coordinates
            V->>V: Verify spatial Euclidean distance & Fitts's Law duration constraints
            V->>V: Verify element render-time (opacity, visibility, size, offscreen checks)
            V->>C: Trigger click pre-testing (dispatch mouseover event)
            alt Traps / Honeypots / Dynamic Movement Detected
                V->>V: Raise Anti-Automation Alarm & Reject action
            else Verified Safe
                V->>C: Execute pyautogui human-jitter click with stochastic sub-pixel jitter
            end
        end
    end
```

## 3. Cryptographic Key Derivation & State Protection (Epics 5 & 6)
```mermaid
graph LR
    PW["User Master Password / Key"] --> KDF["Argon2id KDF: derive_key_argon2id"]
    RAM["System Physical RAM Detection"] -->|Scales Memory Cost: 64MB - 512MB| KDF
    CORES["System CPU Cores Count"] -->|Parallelism: p = min(cores-1, 4)| KDF
    KDF -->|Derives 256-bit Key| AES["AES-GCM Encryption Engine"]
    HW["Local Hardware Fingerprint: CPU, user, volume serial"] -->|verify_identity_integrity| AES
    TPM["TPM 2.0 Silicon sealing"] -->|Seals derived keys| AES
    DPAPI["Windows DPAPI Secure Backup"] -->|Fallback if TPM absent| AES
    AES -->|Encrypts| DB[("sessions_registry.db: clock_json, data_dir, value")]
    AES -->|Encrypts| CFG[("settings.json: GUI state, credentials")]
```

## 4. Log Ingestion Validation Pipeline (Epic 7)
```mermaid
flowchart TD
    Raw["Raw Log Input (Folder/Files)"] --> Parallel["Parallel Scanner (ThreadPoolExecutor)"]
    Parallel --> Parse["Extract Credentials (email:pass, login:pass, blocks)"]
    Parse --> Pipe["IngestionPipeline.process"]
    subgraph Pipe["Ingestion Pipeline Stage Validation"]
        Schema["Schema Check (Regex email pattern validation)"]
        Length["Value Check (Password length >= 4)"]
        Honey["Honeypot/Trap Check (Rejects fake, decoy, trap tags)"]
        Sig["Signature/Checksum Check (Stable SHA-256 checks)"]
        Schema --> Length --> Honey --> Sig
    end
    Pipe --> Threshold{"Invalid Records > 5%?"}
    Threshold -->|Yes| Reject["REJECT Entire Batch (ValueError)"]
    Threshold -->|No| Insert["WAL-Mode Batched Populate DB"]
    Insert --> Commit["Commit Transaction (BATCH = 500)"]
```

## 5. Fingerprint Generation & Bayesian Dirichlet Calibration (Epic 1)
```mermaid
flowchart TD
    Start["Start Fingerprint Generation"] --> Copula["Gaussian Copula Correlation (TLS, TCP window, H2 frame, JS latency)"]
    Copula --> Weight["Dirichlet Bayesian Updater (Get prior weights w_i)"]
    Weight --> Reference["Generate Stratified Reference: R = sum(w_i * D_i)"]
    Reference --> Divergence["Two-Sided KL Divergence Validation"]
    subgraph Divergence["Two-Sided KL Divergence Constraints"]
        Rejection["Rejection boundary: D_KL(P || D4) > 0.8 (Compromised nodes)"]
        Resemblance["Resemblance boundary: D_KL(P || R) < 0.5 (Target distribution)"]
    end
    Divergence --> Decision{"Is Fingerprint Valid?"}
    Decision -->|Yes| Registry["Write rotated HWID (MachineGuid/DigitalProductId)"]
    Decision -->|No| UpdateWeights["Bayesian Update (Success +1.0, Block -0.2)"]
    UpdateWeights --> Weight
```

## 6. Stochastic Expected-Utility Scheduling (Epic 4)
```mermaid
flowchart TD
    Queue["Queue Tasks"] --> Iterate["Select Task from Queue"]
    Iterate --> CB{"Circuit Breaker CLOSED/HALF-OPEN?"}
    CB -->|No| Skip["Quarantine Task; bypass execution"]
    CB -->|Yes| MonteCarlo["Monte Carlo LogNormal Latency Simulation (Ti ~ LogNormal)"]
    MonteCarlo --> Utility["Calculate Expected Utility: E[Ui] = value * E[e^-lateness]"]
    Utility --> MaxUtility["Execute task with Max E[Ui]"]
    MaxUtility --> Measure["Measure Task Execution Duration"]
    Measure --> UpdateEstimates["Update LogNormal parameters (mu, sigma) via moving average"]
    UpdateEstimates --> Outcome{"Task Succeeded?"}
    Outcome -->|Yes| RecordSuccess["Record Success (Close breaker)"]
    Outcome -->|No| RecordFailure["Record Failure (Increment failures / Open breaker)"]
```
"""

# Exclusion criteria
EXCLUDE_DIRS = {
    'node_modules', '.venv', 'venv', 'env', '.git', 'temp_sessions', 
    '_ext_unpacked', '_ext_test_7z', '_ext_test_output', 'output', 
    'scratch', '.vscode', '.idea', 'DELETED', 'all_results', 'RESULTS', 
    '__pycache__', 'media', 'data', 'configs'
}

EXCLUDE_PREFIXES = ('results_', '2026-')

EXCLUDE_FILES = {
    'settings.json', 'settings.json.bak', 'settings.pkl', 'settings.pkl.bak',
    'discovery_results.db', 'checked.db', 'test_accounts.db', 'ocr_results.txt',
    'System_Audit_Log.json', 'application.log', 'auto_click_direct.log',
    'auto_click_v2.log', 'tab_monitor.log', 'validator_captured.log',
    'validator_direct.log', 'validator_err.log', 'run_validator_captured_out.log',
    'chromedriver.exe', 'chromedriver143.exe', 'honeygain.txt', 
    'mercadolivre1.txt', 'miniapps_clean.txt', 'pastebin.com_userpass.txt',
    'codebase_export.md', 'plan.md', 'export_architecture.py'
}

ALLOWED_EXTENSIONS = {'.py', '.md', '.txt', '.json', '.js', '.mjs', '.ts', '.sh'}

def redact_secrets(content: str) -> str:
    """Redacts potential secrets, passwords, or API keys from strings."""
    # Redact variables containing tokens or keys followed by string literals
    patterns = [
        (r'(?i)(api_key|openrouter_key|token|bot_token|password|pass|secret|auth)\s*(=|:)\s*([\'"])(?:[^\'"]{4,})(?:\3)', r'\1 \2 \3[REDACTED]\3'),
        (r'(?i)(telegram_token|client_secret)\s*=\s*([\'"])[^\'"]+\2', r'\1 = \2[REDACTED]\2')
    ]
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    return content

def should_process_dir(dir_name: str) -> bool:
    """Checks if directory should be skipped."""
    if dir_name in EXCLUDE_DIRS:
        return False
    for prefix in EXCLUDE_PREFIXES:
        if dir_name.startswith(prefix):
            return False
    return True

def should_process_file(file_name: str) -> bool:
    """Checks if file should be skipped."""
    if file_name in EXCLUDE_FILES:
        return False
    ext = os.path.splitext(file_name)[1]
    return ext in ALLOWED_EXTENSIONS

def generate_export():
    project_root = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(project_root, 'codebase_export.md')
    
    print(f"Starting codebase export from: {project_root}")
    print(f"Export target path: {output_path}")
    
    with open(output_path, 'w', encoding='utf-8', newline='\n') as out:
        out.write("# UC Codebase Architecture & Source Export\n\n")
        out.write("This file contains the complete codebase source and architectural flowcharts for NotebookLM research.\n\n")
        
        # Inject Mermaid diagrams
        out.write(MERMAID_DIAGRAMS)
        
        # Walk directories
        file_count = 0
        for root, dirs, files in os.walk(project_root):
            # Prune directories in-place to prevent os.walk from entering them
            dirs[:] = [d for d in dirs if should_process_dir(d)]
            
            for file in sorted(files):
                if not should_process_file(file):
                    continue
                
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, project_root).replace('\\', '/')
                
                print(f"Exporting: {rel_path}")
                
                try:
                    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                        raw_content = f.read()
                    
                    redacted = redact_secrets(raw_content)
                    
                    # Markdown section header
                    out.write(f"## File: `{rel_path}`\n\n")
                    
                    # Language identifier for syntax highlighting
                    ext = os.path.splitext(file)[1]
                    lang = 'python'
                    if ext == '.md':
                        lang = 'markdown'
                    elif ext in ('.js', '.mjs'):
                        lang = 'javascript'
                    elif ext == '.ts':
                        lang = 'typescript'
                    elif ext == '.sh':
                        lang = 'bash'
                    elif ext == '.json':
                        lang = 'json'
                    elif ext == '.txt':
                        lang = 'text'
                        
                    out.write(f"```{lang}\n")
                    out.write(redacted)
                    if not redacted.endswith('\n'):
                        out.write('\n')
                    out.write("```\n\n---\n\n")
                    
                    file_count += 1
                except Exception as e:
                    print(f"Error reading file {rel_path}: {e}", file=sys.stderr)
                    
        print(f"\nExport complete! Exported {file_count} files successfully.")

if __name__ == '__main__':
    generate_export()
