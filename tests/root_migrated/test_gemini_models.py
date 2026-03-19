#!/usr/bin/env python3
"""
List available Google Gemini models and test them
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv()

import os
import google.generativeai as genai

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY not found in .env")
    sys.exit(1)

genai.configure(api_key=api_key)

print("=" * 80)
print("AVAILABLE GOOGLE GEMINI MODELS")
print("=" * 80)

# List all models
models = genai.list_models()

text_models = []
vision_models = []

for model in models:
    name = model.name
    display_name = model.display_name
    
    # Check capabilities
    can_generate_content = model.supported_generation_methods
    
    if "generateContent" in model.supported_generation_methods:
        # Check if it supports text input
        has_text = any("text" in str(cap).lower() for cap in model.input_content_types if cap)
        
        if has_text:
            text_models.append({
                "name": name,
                "display_name": display_name,
                "model": model
            })

print(f"\n📊 TEXT GENERATION MODELS ({len(text_models)} available):\n")

# Categorize and sort
tier_rankings = {
    "gemini-2.0-flash": 5,
    "gemini-2.0-pro": 4,
    "gemini-1.5-pro": 3,
    "gemini-1.5-flash": 2,
    "gemini-pro": 1,
}

ranked = []
for model_info in text_models:
    name = model_info["name"]
    tier = 0
    for tier_name, tier_val in tier_rankings.items():
        if tier_name in name:
            tier = tier_val
            break
    ranked.append((tier, model_info))

ranked.sort(reverse=True, key=lambda x: x[0])

for tier, model_info in ranked:
    name = model_info["name"]
    display = model_info["display_name"]
    
    # Determine rank
    if "2.0-pro" in name:
        rank = "⭐⭐⭐⭐⭐ BEST (Latest, Most Capable)"
        recommended = "✅ RECOMMENDED"
    elif "2.0-flash" in name:
        rank = "⭐⭐⭐⭐ (Fast & Capable)"
        recommended = "✅ RECOMMENDED"
    elif "1.5-pro" in name:
        rank = "⭐⭐⭐ (Capable, Proven)"
        recommended = "✅ GOOD"
    elif "1.5-flash" in name:
        rank = "⭐⭐ (Fast)"
        recommended = "FAIR"
    else:
        rank = "⭐ (Legacy)"
        recommended = ""
    
    print(f"{rank}")
    print(f"  Model: {name}")
    print(f"  Display: {display}")
    if recommended:
        print(f"  {recommended}")
    print()

# Test the best model
print("=" * 80)
print("TESTING BEST MODEL")
print("=" * 80)

best_model_name = None
for tier, model_info in ranked:
    if "2.0" in model_info["name"] or "1.5-pro" in model_info["name"]:
        best_model_name = model_info["name"]
        break

if not best_model_name and ranked:
    best_model_name = ranked[0][1]["name"]

if best_model_name:
    print(f"\n🧪 Testing: {best_model_name}\n")
    
    model = genai.GenerativeModel(best_model_name)
    
    test_prompt = """Convert this DAX to SQL:
DAX: SUM([Revenue])
Table: sales
Provide only the SQL, no explanation."""
    
    try:
        response = model.generate_content(test_prompt)
        print(f"✅ Model works!")
        print(f"Response: {response.text[:200]}")
    except Exception as e:
        print(f"❌ Error: {e}")
else:
    print("❌ No suitable models found")

print("\n" + "=" * 80)
print("RECOMMENDATION:")
print("=" * 80)

for tier, model_info in ranked[:3]:
    name = model_info["name"]
    if "2.0-pro" in name or "2.0-flash" in name or "1.5-pro" in name:
        print(f"\n🎯 Best choice: {name}")
        print(f"   - Latest Google model")
        print(f"   - Excellent for code translation")
        print(f"   - Free tier available")
        break
