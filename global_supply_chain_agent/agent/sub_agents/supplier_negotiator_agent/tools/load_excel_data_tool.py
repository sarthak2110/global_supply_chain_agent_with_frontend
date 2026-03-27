# tools/load_excel_data_tool.py

import json
import pandas as pd
from pathlib import Path

# data/ is a sibling to tools/
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def _split_semi(s: str) -> list[str]:
    return [t.strip() for t in str(s or "").split(";") if t and t.strip()]

def load_data_from_excel(
    suppliers_path: str | None = None,
    policy_path: str | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """
    Loads suppliers, catalog, quotes and finance policy from Excel in data/.
    Expects:
      data/suppliers.xlsx: sheets 'suppliers', 'catalog', 'quotes'
      data/finance_policy.xlsx: sheets 'policy', 'po_defaults'
    Returns: (suppliers_list, quotes_list, finance_policy_dict)
    """
    suppliers_path = Path(suppliers_path) if suppliers_path else (DATA_DIR / "suppliers.xlsx")
    policy_path = Path(policy_path) if policy_path else (DATA_DIR / "finance_policy.xlsx")

    # --- Suppliers workbook ---
    swb = pd.ExcelFile(str(suppliers_path), engine="openpyxl")
    suppliers_df = pd.read_excel(swb, "suppliers")
    catalog_df = pd.read_excel(swb, "catalog")
    quotes_df = pd.read_excel(swb, "quotes")

    # Nest catalog rows under each supplier
    suppliers_list: list[dict] = []
    for _, s in suppliers_df.iterrows():
        s_id = s["supplier_id"]
        items = catalog_df[catalog_df["supplier_id"] == s_id].to_dict(orient="records")
        suppliers_list.append({
            "id": s["supplier_id"],
            "name": s["supplier_name"],
            "contact_email": s["contact_email"],
            "country": s["country"],
            "rating": float(s["rating"]),
            "payment_terms": s["payment_terms"],
            "on_time_pct": float(s.get("on_time_pct", 0.0)),
            "defect_rate_pct": float(s.get("defect_rate_pct", 0.0)),
            "items": items,
        })

    quotes_list: list[dict] = quotes_df.to_dict(orient="records")

    # --- Finance policy workbook ---
    pwb = pd.ExcelFile(str(policy_path), engine="openpyxl")
    policy_kv_df = pd.read_excel(pwb, "policy")
    po_defaults_df = pd.read_excel(pwb, "po_defaults")

    policy_map = dict(zip(policy_kv_df["key"], policy_kv_df["value"]))
    po_defaults_map = dict(zip(po_defaults_df["field"], po_defaults_df["value"]))

    # Normalize types
    finance_policy = {
        "currency": policy_map.get("currency", "USD"),
        "max_without_approval": float(policy_map.get("max_without_approval", 10000)),
        "preferred_payment_terms": _split_semi(policy_map.get("preferred_payment_terms")),
        "min_supplier_rating": float(policy_map.get("min_supplier_rating", 4.0)),
        "preferred_incoterms": _split_semi(policy_map.get("preferred_incoterms")),
        "disallowed_incoterms": _split_semi(policy_map.get("disallowed_incoterms")),
        "approval_contacts": _split_semi(policy_map.get("approval_contacts")),
        "po_number_format": policy_map.get("po_number_format", "AUTO-YYYYMMDD-SEQ4"),
        "po_defaults": {
            "bill_to": po_defaults_map.get("bill_to", ""),
            "ship_to": po_defaults_map.get("ship_to", ""),
            "tax_rate": float(po_defaults_map.get("tax_rate", 0.0)),
            "shipping_cost": float(po_defaults_map.get("shipping_cost", 0.0)),
        }
    }

    return suppliers_list, quotes_list, finance_policy


def to_json_blobs(
    suppliers_list: list[dict], quotes_list: list[dict], finance_policy: dict
) -> tuple[str, str, str]:
    return (
        json.dumps(suppliers_list, ensure_ascii=False, indent=2),
        json.dumps(quotes_list, ensure_ascii=False, indent=2),
        json.dumps(finance_policy, ensure_ascii=False, indent=2),
    )