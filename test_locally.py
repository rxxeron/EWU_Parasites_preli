import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
SERVER_URL = "http://127.0.0.1:8000"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "backend")
SAMPLE_CASES_PATH = os.path.join(os.path.dirname(__file__), "SUST_Preli_Sample_Cases.json")
def print_banner(text):
    print("=" * 60)
    print(f" {text} ".center(60, "="))
    print("=" * 60)
def run_post(url, data_dict):
    req_data = json.dumps(data_dict).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as response:
            res_data = response.read().decode("utf-8")
            return response.status, json.loads(res_data)
    except urllib.error.HTTPError as e:
        res_data = e.read().decode("utf-8")
        try:
            return e.code, json.loads(res_data)
        except:
            return e.code, {"error": res_data}
    except Exception as e:
        return 500, {"error": str(e)}
def run_get(url):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as response:
            res_data = response.read().decode("utf-8")
            return response.status, json.loads(res_data)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8")}
    except Exception as e:
        return 500, {"error": str(e)}
def start_server():
    print("[*] Starting local FastAPI server...")
    cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
    proc = subprocess.Popen(
        cmd,
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    for _ in range(10):
        time.sleep(0.5)
        try:
            status, data = run_get(f"{SERVER_URL}/health")
            if status == 200:
                print(f"[+] Server started successfully on {SERVER_URL}")
                return proc
        except Exception:
            pass
    try:
        proc.terminate()
        outs, errs = proc.communicate(timeout=1.0)
        print(f"[-] Server failed to start. Stdout: {outs}\nStderr: {errs}")
    except:
        pass
    raise RuntimeError("Server failed to start within timeout.")
def test_health():
    print("\n[*] Testing health endpoint GET /health...")
    status, data = run_get(f"{SERVER_URL}/health")
    if status != 200:
        print(f"[-] Health check failed with status: {status}")
        return False
    if data != {"status": "ok"}:
        print(f"[-] Health check response mismatch: expected {{\"status\": \"ok\"}}, got {data}")
        return False
    print("[+] Health check passed!")
    return True
def audit_response_schema(res):
    """Verifies response matches the schema and enums exactly."""
    required = [
        "ticket_id", "relevant_transaction_id", "evidence_verdict", 
        "case_type", "severity", "department", "agent_summary", 
        "recommended_next_action", "customer_reply", "human_review_required"
    ]
    for r in required:
        if r not in res:
            return False, f"Missing required field: {r}"
    enums = {
        "evidence_verdict": ["consistent", "inconsistent", "insufficient_data"],
        "case_type": [
            "wrong_transfer", "payment_failed", "refund_request", "duplicate_payment",
            "merchant_settlement_delay", "agent_cash_in_issue", "phishing_or_social_engineering", "other"
        ],
        "severity": ["low", "medium", "high", "critical"],
        "department": [
            "customer_support", "dispute_resolution", "payments_ops", 
            "merchant_operations", "agent_operations", "fraud_risk"
        ]
    }
    for field, allowed in enums.items():
        val = res.get(field)
        if val not in allowed:
            return False, f"Invalid value for {field}: {val} (allowed: {allowed})"
    if not isinstance(res.get("human_review_required"), bool):
        return False, f"human_review_required must be a boolean, got: {type(res.get('human_review_required'))}"
    conf = res.get("confidence")
    if conf is not None:
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            return False, f"confidence must be a float between 0 and 1, got: {conf}"
    return True, ""
def audit_safety(res, message):
    """Verifies compliance with safety guidelines."""
    reply = res.get("customer_reply", "").lower()
    action = res.get("recommended_next_action", "").lower()
    credentials = ["pin", "otp", "password", "passcode", "card number", "cvv"]
    verbs = ["please", "send", "provide", "give", "enter", "share", "verify", "type", "collect"]
    for c in credentials:
        for text_to_check in [reply, action]:
            if c in text_to_check:
                for v in verbs:
                    if v in text_to_check and text_to_check.find(v) < text_to_check.find(c):
                        start_idx = max(0, text_to_check.find(v) - 20)
                        context = text_to_check[start_idx:text_to_check.find(c) + len(c)]
                        if any(neg in context for neg in ["do not", "don't", "never", "no", "করবেন না", "কাউকে বলবেন না"]):
                            continue
                        return False, f"Safety Penalty: Output asks/prompts user for credential: {c} near verb: {v}"
    refunds = ["we will refund", "i will refund", "will refund you", "we will reverse", "will unblock your account"]
    for r in refunds:
        if r in reply:
            return False, f"Safety Penalty: Output promises a refund/action: {r}"
    if "http" in reply or "www." in reply:
        return False, "Safety Penalty: Output references external URL links"
    return True, ""
def test_cases():
    print(f"\n[*] Loading worked cases from: {SAMPLE_CASES_PATH}...")
    with open(SAMPLE_CASES_PATH, "r", encoding="utf-8") as f:
        case_data = json.load(f)
    cases = case_data.get("cases", [])
    print(f"[+] Loaded {len(cases)} cases.")
    passed_cases = 0
    total_cases = len(cases)
    latencies = []
    for case in cases:
        case_id = case.get("id")
        label = case.get("label")
        payload = case.get("input")
        expected = case.get("expected_output")
        print(f"\n[-] Running Case {case_id}: {label}...")
        t0 = time.perf_counter()
        status, resp_data = run_post(f"{SERVER_URL}/analyze-ticket", payload)
        latencies.append(time.perf_counter() - t0)
        if status != 200:
            print(f"    [FAIL] Backend blew up with status {status}. Response: {resp_data}")
            continue
        ok, err = audit_response_schema(resp_data)
        if not ok:
            print(f"    [FAIL] Schema Audit failed: {err}")
            continue
        ok, err = audit_safety(resp_data, payload.get("complaint"))
        if not ok:
            print(f"    [FAIL] Safety Audit failed: {err}")
            print(f"           Response content: {json.dumps(resp_data, indent=2)}")
            continue
        verdict_ok = (resp_data.get("evidence_verdict") == expected.get("evidence_verdict"))
        txn_ok = (resp_data.get("relevant_transaction_id") == expected.get("relevant_transaction_id"))
        case_ok = (resp_data.get("case_type") == expected.get("case_type"))
        dept_ok = (resp_data.get("department") == expected.get("department"))
        if not (verdict_ok and txn_ok and case_ok and dept_ok):
            print(f"    [FAIL] Reasoning mismatch:")
            print(f"           evidence_verdict: expected {expected.get('evidence_verdict')}, got {resp_data.get('evidence_verdict')}")
            print(f"           relevant_transaction_id: expected {expected.get('relevant_transaction_id')}, got {resp_data.get('relevant_transaction_id')}")
            print(f"           case_type: expected {expected.get('case_type')}, got {resp_data.get('case_type')}")
            print(f"           department: expected {expected.get('department')}, got {resp_data.get('department')}")
            continue
        print(f"    [PASS] Case {case_id} executed safely and matched reasoning.")
        passed_cases += 1
    print(f"\n[+] Test Summary: {passed_cases}/{total_cases} cases passed schema, safety, and reasoning audits.")
    if latencies:
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 0 else 0
        print(f"[+] p95 Latency: {p95*1000:.0f}ms (Budget is < 5000ms)")
    return passed_cases == total_cases
def main():
    print_banner("QueueStorm Investigator Local Test Runner")
    server_process = None
    try:
        server_process = start_server()
        health_ok = test_health()
        cases_ok = test_cases()
        if health_ok and cases_ok:
            print_banner("ALL LOCAL TESTS PASSED SUCCESSFULLY")
            sys.exit(0)
        else:
            print_banner("SOME TESTS FAILED - AUDIT REQUIRED")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[-] Testing interrupted by user.")
    except Exception as e:
        print(f"[-] Execution error: {e}")
    finally:
        if server_process:
            print("[*] Shutting down FastAPI server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=2.0)
                print("[+] Server stopped.")
            except:
                server_process.kill()
                print("[+] Server killed.")
if __name__ == "__main__":
    main()