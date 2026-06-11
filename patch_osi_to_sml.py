import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\converter\osi_to_sml.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

if 'from semabridge.converter.prompt_generator import DynamicPromptGenerator, ConversionValidator' not in content:
    content = content.replace('from semabridge.converter.dax_translator import DAXTranslator', 
                              'from semabridge.converter.dax_translator import DAXTranslator\nfrom semabridge.converter.prompt_generator import DynamicPromptGenerator, ConversionValidator')

injection_code = '''        try:
            # Generate Dynamic Conversion Prompt (for reference/LLM pipeline)
            try:
                # Convert OSIModel object to dict for the generator
                osi_dict = {
                    "datasets": [{"unique_name": ds.unique_name, "description": ds.description} for ds in osi_model.datasets],
                    "columns": [{"unique_name": col.unique_name, "source_expression": getattr(col, "source_expression", None), "default_aggregation": getattr(col, "default_aggregation", None)} for ds in osi_model.datasets for col in ds.columns],
                    "metrics": [{"unique_name": m.unique_name, "default_aggregation": getattr(m, "aggregation", None)} for m in osi_model.metrics],
                    "relationships": [{"unique_name": r.unique_name} for r in osi_model.relationships]
                }
                generator = DynamicPromptGenerator()
                dynamic_prompt = generator.generate_conversion_prompt(osi_dict)
                logger.info(f"Generated Dynamic Conversion Prompt:\\n{dynamic_prompt}")
            except Exception as e:
                logger.warning(f"Could not generate dynamic prompt: {e}")
                
            sml = SMLModel('''

content = re.sub(
    r'        try:\s+sml = SMLModel\(',
    injection_code,
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched osi_to_sml.py")
