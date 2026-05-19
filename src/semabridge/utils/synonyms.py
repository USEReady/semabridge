from __future__ import annotations
from typing import List
import re


def merge_synonyms(
    ui_overrides:   list = None,
    user_defined:   list = None,
    auto_generated: list = None,
    max_auto:       int  = 3
) -> list:
    """
    Merge synonyms with priority: UI overrides > TMSL/user > auto-generated.
    Deduplicates case-insensitively. Auto-generated capped at max_auto.
    """
    import unicodedata

    # Backward compatibility: if two positional arguments are passed, map them correctly
    if user_defined is not None and auto_generated is None:
        auto_generated = user_defined
        user_defined = ui_overrides
        ui_overrides = None

    result = []
    seen   = set()
 
    # 1. UI overrides — highest priority, always first
    for s in (ui_overrides or []):
        if not isinstance(s, str):
            continue
        s_norm = unicodedata.normalize("NFC", s.strip())
        if s_norm and s_norm.lower() not in seen:
            seen.add(s_norm.lower())
            result.append(s.strip())
 
    # 2. TMSL / Power BI user-defined synonyms
    for s in (user_defined or []):
        if not isinstance(s, str):
            continue
        s_norm = unicodedata.normalize("NFC", s.strip())
        if s_norm and s_norm.lower() not in seen:
            seen.add(s_norm.lower())
            result.append(s.strip())
 
    # 3. Auto-generated synonyms (capped)
    added = 0
    for s in (auto_generated or []):
        if not isinstance(s, str):
            continue
        s_norm = unicodedata.normalize("NFC", s.strip())
        if s_norm and s_norm.lower() not in seen and added < max_auto:
            seen.add(s_norm.lower())
            result.append(s.strip())
            added += 1
 
    return result


def generate_auto_synonyms(name: str) -> list[str]:
    """
    Generate simple synonym candidates from a column/measure name.
    Useful for automated metadata generation when user synonyms are missing.

    Example: "CustomerID" → ["Customer ID"]
             "sale_amount" → ["Sale Amount"]
    """
    # 1. Clean up special characters like () []
    clean_name = re.sub(r'[^a-zA-Z0-9_\s]', ' ', name)
    # 2. Convert snake_case to spaces
    snake_separated = clean_name.replace("_", " ").strip()
    # 3. Handle camelCase, PascalCase, and Acronyms (e.g. VATRate -> VAT Rate)
    # Insert space between lower and upper: aB -> a B
    c1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", snake_separated)
    # Insert space between upper and upper-lower: ABc -> A Bc
    camel_separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", c1)
    
    title_form = camel_separated.title().strip()

    synonyms: list[str] = []
    if title_form and title_form.lower() != name.lower():
        synonyms.append(title_form)

    # Common business semantic expansions (aliases)
    _semantic_map = {
        "id": ["Identifier", "Key"],
        "cust": ["Customer", "Client"],
        "customer": ["Client", "Purchaser"],
        "acct": ["Account"],
        "amt": ["Amount", "Value"],
        "qty": ["Quantity", "Volume"],
        "quantity": ["Volume", "Count"],
        "num": ["Number"],
        "desc": ["Description", "Detail"],
        "dt": ["Date"],
        "yr": ["Year"],
        "mth": ["Month"],
        "qtr": ["Quarter"],
        "wk": ["Week"],
        "sales": ["Revenue", "Income"],
        "revenue": ["Sales", "Turnover"],
        "count": ["Total Number", "Tally"],
        "total": ["Sum", "Aggregate"],
        "units": ["Volume", "Quantity"],
        "sentiment": ["Feedback", "Opinion", "Rating"],
        "cost": ["Expense", "Expenditure"],
        "price": ["Rate", "Value"],
        "profit": ["Margin", "Gain"],
        "region": ["Area", "Territory"],
        "cat": ["Category"],
        "category": ["Type", "Class", "Grouping"],
        "prod": ["Product"],
        "product": ["Item", "Good", "Merchandise"],
        "ytd": ["Year To Date"],
        "mtd": ["Month To Date"],
        "qtd": ["Quarter To Date"],
        "mfg": ["Manufacturing", "Manufacturer"],
        "manufacturer": ["Producer", "Maker"],
        "vendor": ["Supplier", "Provider"],
    }
    
    title_words = title_form.split()
    for word in title_words:
        w_lower = word.lower()
        if w_lower in _semantic_map:
            for alias in _semantic_map[w_lower]:
                # Replace whole word matches with the alias
                pattern = rf"\b{re.escape(word)}\b"
                expanded = re.sub(pattern, alias, title_form, flags=re.IGNORECASE)
                if expanded.lower() != name.lower() and expanded not in synonyms:
                    synonyms.append(expanded)

    # Return deduplicated list (up to 5 synonyms)
    seen: list[str] = []
    for s in synonyms:
        if s not in seen and s.lower() != name.lower():
            seen.append(s)
    return seen[:5]


def load_synonym_overrides(project_id: str) -> dict:
    """
    Load all manually entered synonyms for a project into a single memory cache.
    Key: (model_name, table_name, column_name) -> list of synonyms.
    """
    from semabridge.utils.logger import get_logger
    log = get_logger(__name__)

    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.models import SynonymOverride

        session = db_manager.get_session_factory()()
        try:
            from sqlalchemy import or_
            rows = session.query(SynonymOverride).filter(
                or_(
                    SynonymOverride.project_id == project_id,
                    SynonymOverride.model_name == project_id
                )
            ).all()
            cache = {}
            for r in rows:
                cache[(r.model_name, r.table_name, r.column_name)] = r.synonyms or []
            log.info(f"Loaded {len(cache)} synonym overrides for project {project_id}")
            return cache
        finally:
            session.close()
    except Exception as e:
        log.warning(f"Could not load synonym overrides: {e}")
        return {}


def save_synonym_override(
    project_id: str,
    model_name: str,
    table_name: str,
    column_name: str,
    synonyms: list[str]
) -> None:
    """
    Upsert manual synonym override in database.
    """
    from semabridge.utils.logger import get_logger
    log = get_logger(__name__)

    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.models import SynonymOverride

        session = db_manager.get_session_factory()()
        try:
            # Check if there is an existing override
            row = session.query(SynonymOverride).filter_by(
                project_id=project_id,
                model_name=model_name,
                table_name=table_name,
                column_name=column_name
            ).first()
            if row:
                row.synonyms = synonyms
            else:
                row = SynonymOverride(
                    project_id=project_id,
                    model_name=model_name,
                    table_name=table_name,
                    column_name=column_name,
                    synonyms=synonyms
                )
                session.add(row)
            session.commit()
            log.info(f"Saved synonym override {synonyms} for {model_name}.{table_name}.{column_name}")
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    except Exception as e:
        log.error(f"Could not save synonym override: {e}")

