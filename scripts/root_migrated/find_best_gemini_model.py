#!/usr/bin/env python3
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=api_key)

models = list(genai.list_models())
print("=" * 80)
print("AVAILABLE GEMINI MODELS")
print("=" * 80)

for model in models[:15]:
    print(f"\nModel: {model.name}")
    print(f"Display: {model.display_name}")

print("\n" + "=" * 80)
print("SELECTING BEST MODEL")
print("=" * 80)

# Find best model
best_model = None
best_name = None

for model in models:
    if 'gemini-2.0-pro' in model.name:
        best_model = model
        best_name = model.name
        print(f"\n✅ Selected: {model.name}")
        print(f"   Latest, most capable model")
        break

if not best_model:
    for model in models:
        if 'gemini-2.0-flash' in model.name:
            best_model = model
            best_name = model.name
            print(f"\n✅ Selected: {model.name}")
            print(f"   Fast and capable")
            break

if not best_model:
    for model in models:
        if 'gemini-1.5-pro' in model.name:
            best_model = model
            best_name = model.name
            print(f"\n✅ Selected: {model.name}")
            print(f"   Proven performance")
            break

if best_model:
    print(f"\nTesting model: {best_name}")
    
    model = genai.GenerativeModel(best_name)
    test_dax = "SUM([Revenue])"
    response = model.generate_content(f"Convert DAX to SQL:\n{test_dax}\nProvide only SQL, no explanation.")
    
    print(f"Test response: {response.text[:100]}")
    print(f"\n✅ Model working and tested!")
else:
    print("\n❌ No suitable model found")
