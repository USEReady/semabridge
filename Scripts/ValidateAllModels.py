"""
Dynamic Model Validation Script for Semabridge.

Sequentially validates multiple models in a loop, preventing API burst collisions.
Models can be loaded from a project configuration YAML or passed directly.
"""

import sys
import os
import time
import argparse
import requests
import yaml
from dotenv import load_dotenv

# Load environment variables (e.g., from .env)
load_dotenv()

def load_models_from_config(config_path):
    """Load model names from a Semabridge project YAML config."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                print(f"Error: Config at {config_path} is not a valid YAML mapping")
                sys.exit(1)
            
            models = data.get("source", {}).get("models", [])
            if not models:
                # Fallback to check if a single model is configured under source.model or model_name
                single_model = data.get("source", {}).get("model") or data.get("model_name")
                if single_model:
                    models = [single_model]
            return models
    except Exception as e:
        print(f"Error parsing config file {config_path}: {e}")
        sys.exit(1)

def validate_single_model(session, api_url, endpoint, access_token, csrf_token, model_name):
    """Validate a single model and return the HTTP status code, status message, and time taken."""
    start_time = time.perf_counter()
    status_code = None
    validation_status = "failed"
    
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        
    try:
        response = session.post(endpoint, json={}, headers=headers, timeout=60)
        status_code = response.status_code
        time_taken = time.perf_counter() - start_time
        
        if response.status_code == 200:
            result = response.json()
            errors = result.get("errors", [])
            # Find errors matching model name
            model_errors = [e for e in errors if e.get("model", "").strip().lower() == model_name.strip().lower()]
            if model_errors:
                validation_status = "failed"
                print(f"[{model_name}] Errors found:")
                for err in model_errors:
                    print(f"  - [{err.get('severity', 'error')}] {err.get('message')}")
            else:
                validation_status = "success"
        else:
            validation_status = "failed"
            print(f"[{model_name}] API returned non-200 status code: {status_code}")
            
    except requests.exceptions.RequestException as e:
        time_taken = time.perf_counter() - start_time
        status_code = "ConnectionError"
        validation_status = "failed"
        print(f"[{model_name}] Connection failed: {e}")
        
    return status_code, validation_status, time_taken

def main():
    parser = argparse.ArgumentParser(description="Validate Semabridge models sequentially.")
    parser.add_argument("--config", help="Path to project YAML config file containing models")
    parser.add_argument("--models", nargs="+", help="Explicit list of model names to validate")
    parser.add_argument("--api-url", help="Semabridge API base URL", default=os.getenv("SEMABRIDGE_API_URL", "http://127.0.0.1:8001"))
    args = parser.parse_args()
    
    api_url = args.api_url.rstrip("/")
    endpoint = f"{api_url}/api/config/validate-live"
    
    # 1. Resolve which models to validate
    models_to_validate = []
    if args.models:
        models_to_validate = args.models
    elif args.config:
        models_to_validate = load_models_from_config(args.config)
    else:
        # Default behavior: if no config or model list is specified, fetch the list from validate-live first
        print("No models or config path provided. Querying active models from validation API...")
    
    session = requests.Session()
    
    # Authenticate to get JWT token
    login_url = f"{api_url}/auth/auto-login"
    access_token = None
    try:
        login_response = session.post(login_url, json={}, timeout=10)
        if login_response.status_code == 200:
            access_token = login_response.json().get("access_token")
        else:
            print(f"Auto-login failed with status {login_response.status_code}: {login_response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to connect for auto-login at {login_url}: {e}")
        
    # Get CSRF token
    health_url = f"{api_url}/api/health"
    try:
        session.get(health_url, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch CSRF token from {health_url}: {e}")
    csrf_token = session.cookies.get("csrf_token", "")
    
    # If no models resolved yet, fetch all available models by doing a preliminary call
    if not models_to_validate:
        try:
            headers = {}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token
            res = session.post(endpoint, json={}, headers=headers, timeout=60)
            if res.status_code == 200:
                errors = res.json().get("errors", [])
                warnings = res.json().get("warnings", [])
                model_names = set()
                for e in errors + warnings:
                    if e.get("model") and e.get("model") != "system":
                        model_names.add(e.get("model"))
                models_to_validate = sorted(list(model_names))
        except Exception as e:
            print(f"Failed to fetch default models: {e}")
            
    if not models_to_validate:
        print("No models found to validate.")
        sys.exit(0)
        
    print(f"Sequentially validating {len(models_to_validate)} models...")
    
    results = []
    any_failed = False
    
    for model_name in models_to_validate:
        print(f"\n---> Validating {model_name}...")
        status_code, validation_status, time_taken = validate_single_model(
            session, api_url, endpoint, access_token, csrf_token, model_name
        )
        results.append({
            "model": model_name,
            "status": validation_status,
            "code": status_code,
            "time": time_taken
        })
        if validation_status == "failed":
            any_failed = True
            
    # Print clean summary table
    print("\n" + "="*80)
    print(f"{'MODEL NAME':<45} | {'STATUS':<10} | {'HTTP CODE':<10} | {'TIME TAKEN':<10}")
    print("="*80)
    for res in results:
        status_str = res["status"].upper()
        time_str = f"{res['time']:.2f}s"
        print(f"{res['model']:<45} | {status_str:<10} | {str(res['code']):<10} | {time_str:<10}")
    print("="*80)
    
    if any_failed:
        print("\n[FAILED] Validation failed for one or more models.")
        sys.exit(1)
    else:
        print("\n[SUCCESS] All models validated successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
