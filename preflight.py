import os
import sys

def main():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if not os.path.exists(env_path):
        env_path = '.env'
        if not os.path.exists(env_path):
            print("[+] Preflight Check: No .env file found. Safe to submit.")
            sys.exit(0)

    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check for real keys
    if "gsk_" in content or "AIza" in content:
        print("\n[!] WARNING: Your .env file contains real API keys (gsk_ or AIza).")
        print("[!] DO NOT ZIP AND SUBMIT THIS DIRECTORY WITH THE .env FILE.")
        print("[!] Please remove the keys or delete the .env file before creating your submission zip.")
        sys.exit(1)
    
    print("[+] Preflight Check: .env file is safe (no real keys found).")
    sys.exit(0)

if __name__ == "__main__":
    main()
