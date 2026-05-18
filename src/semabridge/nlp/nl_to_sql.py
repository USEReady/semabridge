"""
Natural Language to SQL (NL-to-SQL) Engine.

Converts natural language questions into semantically correct 
Snowflake SQL using the project's semantic model and LLM reasoning.
"""

from typing import Optional, Dict
import hashlib
from semabridge.converter.llm_translator import LlmTranslator
from semabridge.nlp.semantic_model_exporter import SemanticModelExporter
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class NLToSQL:
    """
    Translates Natural Language to SQL using Semantic Model context.
    """
    
    def __init__(self, model: str = "gemini-pro"):
        self.llm = LlmTranslator(model=model)
        self.exporter = SemanticModelExporter()
        self.cache = {}

    def question_to_sql(self, question: str, project_id: str, metadata: Dict) -> str:
        """
        Main entry point for NL to SQL translation.
        """
        # 1. Check cache
        q_hash = hashlib.md5(f"{question}_{project_id}".encode()).hexdigest()
        if q_hash in self.cache:
            logger.info("NLP Cache Hit")
            return self.cache[q_hash]
            
        # 2. Export Semantic Model context
        json_model = self.exporter.export_project_model(project_id, metadata)
        
        # 3. Build Prompt
        prompt = f"""
You are a Snowflake SQL expert. Convert the natural language question to SQL 
using the semantic model provided below.

Semantic Model:
{json_model}

Question: {question}

Rules:
- Use only measures, dimensions, tables from the semantic model.
- For time phrases ("last year", "YTD"), use Snowflake date functions.
- Output ONLY the SQL query, no markdown, no explanations.

SQL:
"""
        # 4. Call LLM
        sql = self.llm.translate(prompt)
        
        if sql:
            # 5. Cache result
            self.cache[q_hash] = sql
            
        return sql or "/* ERROR: NL to SQL Translation Failed */"
