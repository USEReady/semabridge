#!/usr/bin/env python
"""
Test script to check available OpenAI models for the given API key.
"""
import os
import sys

def list_available_models():
    """List all available models from OpenAI API."""
    try:
        import openai
        api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            print("[ERROR] OPENAI_API_KEY environment variable not set")
            return False
        
        client = openai.OpenAI(api_key=api_key)
        
        print("Fetching available models from OpenAI API...")
        print("=" * 80)
        
        models = client.models.list()
        
        # Categorize models
        gpt_models = []
        embedding_models = []
        other_models = []
        
        for model in models:
            model_id = model.id
            
            if 'gpt' in model_id.lower():
                gpt_models.append(model_id)
            elif 'embed' in model_id.lower():
                embedding_models.append(model_id)
            else:
                other_models.append(model_id)
        
        # Display GPT models (suitable for DAX translation)
        print("\nGPT MODELS (Suitable for DAX Translation):")
        print("-" * 80)
        if gpt_models:
            for model in sorted(gpt_models):
                print(f"  ✓ {model}")
        else:
            print("  [No GPT models available]")
        
        # Display embedding models
        print("\nEMBEDDING MODELS:")
        print("-" * 80)
        if embedding_models:
            for model in sorted(embedding_models):
                print(f"  - {model}")
        else:
            print("  [No embedding models available]")
        
        # Display other models
        if other_models:
            print("\nOTHER MODELS:")
            print("-" * 80)
            for model in sorted(other_models):
                print(f"  - {model}")
        
        # Recommendations
        print("\n" + "=" * 80)
        print("RECOMMENDATIONS FOR DAX TRANSLATION:")
        print("=" * 80)
        
        recommendations = []
        
        # Check for GPT-4
        gpt4_models = [m for m in gpt_models if 'gpt-4' in m.lower()]
        if gpt4_models:
            print(f"\n[TIER 1 - BEST] GPT-4 Models:")
            for model in sorted(gpt4_models):
                print(f"  Recommended: {model}")
                recommendations.append(("gpt-4", model, "Most accurate, higher cost"))
        
        # Check for GPT-3.5-turbo
        gpt35_models = [m for m in gpt_models if 'gpt-3.5' in m.lower()]
        if gpt35_models:
            print(f"\n[TIER 2 - GOOD] GPT-3.5-Turbo Models:")
            for model in sorted(gpt35_models):
                print(f"  Recommended: {model}")
                recommendations.append(("gpt-3.5", model, "Good balance of speed/cost/accuracy"))
        
        # Check for other GPT models
        other_gpt = [m for m in gpt_models if m not in gpt4_models + gpt35_models]
        if other_gpt:
            print(f"\n[TIER 3 - ALTERNATIVE] Other GPT Models:")
            for model in sorted(other_gpt):
                print(f"  Alternative: {model}")
        
        print("\n" + "=" * 80)
        print("IMPLEMENTATION GUIDE:")
        print("=" * 80)
        
        if recommendations:
            best_model = recommendations[0][1]
            print(f"\nUpdate src/semabridge/converter/llm_dax_translator.py line 57:")
            print(f"  self.model = \"{best_model}\"")
            print(f"\nThen test with:")
            print(f"  $env:OPENAI_API_KEY='your-api-key'")
            print(f"  python tests/test_measure_pipeline_detailed.py --dataset Core_Finance_v1")
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to list models: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\nOpenAI Model Availability Tester")
    print("=" * 80)
    
    success = list_available_models()
    
    if not success:
        print("\nTroubleshooting:")
        print("  1. Verify OPENAI_API_KEY is set in environment")
        print("  2. Check API key is valid at https://platform.openai.com/api-keys")
        print("  3. Verify you have credits/valid billing at https://platform.openai.com/account/billing/overview")
        sys.exit(1)
    else:
        print("\n[OK] Model listing successful!")
