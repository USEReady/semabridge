"""Zero-dependency PDF & Excel (XLSX) Evidence Pack generators for Certification Runs."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape


def generate_audit_trail_id() -> str:
    return f"AUD-{uuid.uuid4().hex[:12].upper()}"


def _pdf_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def build_evidence_pdf(
    records: List[Dict[str, Any]],
    *,
    title: str = "SemaBridge BI-to-Snowflake Certification Evidence Pack",
    audit_trail_id: Optional[str] = None,
    project_id: Optional[str] = None,
    approver: Optional[str] = None,
    approved_at: Optional[str] = None,
) -> bytes:
    """Build a valid multi-page PDF 1.4 document with run details, sign-off & audit ID."""
    audit_id = audit_trail_id or generate_audit_trail_id()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = [
        title,
        f"Audit Trail ID: {audit_id}",
        f"Generated: {generated}",
        f"Project: {project_id or 'All Projects'}",
        f"Approver Sign-Off: {approver or 'Pending sign-off'}" + (f" ({approved_at})" if approved_at else ""),
        "-" * 92,
        f"{'Metric':<32} {'Status':<14} {'DAX/SQL Rows':<14} {'Max Delta %':<12} {'Badge'}",
        "-" * 92,
    ]
    for r in records:
        metric = str(r.get("metric_name") or "")[:30]
        status = str(r.get("status") or "")[:12]
        rows_str = f"{r.get('dax_row_count', 0)}/{r.get('sql_row_count', 0)}"
        delta = f"{float(r.get('max_percentage_delta') or 0.0):.4f}%"
        badge = str(r.get("certification_badge") or r.get("approved_by") or "")[:24]
        lines.append(f"{metric:<32} {status:<14} {rows_str:<14} {delta:<12} {badge}")
        if r.get("approved_by"):
            lines.append(f"   Approved by: {r['approved_by']} at {r.get('approved_at', '')} | Audit: {r.get('audit_trail_id', audit_id)}")

    # Paginate 48 lines per page
    pages_lines = [lines[i : i + 48] for i in range(0, max(1, len(lines)), 48)]
    objects: List[bytes] = []

    # 1: Catalog, 2: Pages, 3: Font
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_obj_nums = [4 + idx * 2 for idx in range(len(pages_lines))]
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_lines)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    for idx, plines in enumerate(pages_lines):
        content_num = 5 + idx * 2
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>"
        ).encode("ascii")
        objects.append(page_dict)

        stream_cmds = ["BT", "/F1 9 Tf", "36 756 Td", "14 TL"]
        for li, line in enumerate(plines):
            if li == 0:
                stream_cmds.append(f"({_pdf_escape(line)}) Tj")
            else:
                stream_cmds.append(f"T* ({_pdf_escape(line)}) Tj")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1", "replace")
        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        objects.append(content_obj)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: List[int] = [0]
    for obj_id, obj_body in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{obj_id} 0 obj\n".encode("ascii"))
        buf.write(obj_body)
        buf.write(b"\nendobj\n")

    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return buf.getvalue()


def _xlsx_col_letter(idx: int) -> str:
    res = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(65 + rem) + res
    return res


def _build_sheet_xml(rows: List[List[Any]]) -> bytes:
    body: List[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for r_idx, row in enumerate(rows, start=1):
        body.append(f'<row r="{r_idx}">')
        for c_idx, val in enumerate(row):
            ref = f"{_xlsx_col_letter(c_idx)}{r_idx}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                body.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                body.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val if val is not None else ""))}</t></is></c>')
        body.append("</row>")
    body.append("</sheetData></worksheet>")
    return "".join(body).encode("utf-8")


def build_evidence_xlsx(
    records: List[Dict[str, Any]],
    *,
    title: str = "SemaBridge BI-to-Snowflake Certification Evidence Pack",
    audit_trail_id: Optional[str] = None,
    project_id: Optional[str] = None,
    approver: Optional[str] = None,
    approved_at: Optional[str] = None,
) -> bytes:
    """Build a valid OpenXML .xlsx workbook with Summary and Certification Details sheets."""
    audit_id = audit_trail_id or generate_audit_trail_id()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    summary_rows: List[List[Any]] = [
        ["Field", "Value"],
        ["Report Title", title],
        ["Audit Trail ID", audit_id],
        ["Generated At", generated],
        ["Project ID", project_id or "All Projects"],
        ["Approver Sign-Off", approver or "Pending sign-off"],
        ["Approved At", approved_at or ""],
        ["Total Metrics", len(records)],
        ["Certified Count", sum(1 for r in records if str(r.get("status") or "").upper() == "CERTIFIED")],
    ]

    detail_rows: List[List[Any]] = [[
        "Audit Trail ID",
        "Project ID",
        "Domain",
        "Metric Name",
        "Status",
        "Badge",
        "DAX Rows",
        "SQL Rows",
        "Matched Rows",
        "Mismatched Rows",
        "Max Absolute Delta",
        "Max Percentage Delta",
        "Certified By",
        "Approved By",
        "Approved At",
        "Created At",
    ]]
    for r in records:
        detail_rows.append([
            r.get("audit_trail_id") or audit_id,
            r.get("project_id") or project_id or "",
            r.get("domain") or "",
            r.get("metric_name") or "",
            r.get("status") or "",
            r.get("certification_badge") or "",
            int(r.get("dax_row_count") or 0),
            int(r.get("sql_row_count") or 0),
            int(r.get("matched_rows") or 0),
            int(r.get("mismatched_rows") or 0),
            float(r.get("max_absolute_delta") or 0.0),
            float(r.get("max_percentage_delta") or 0.0),
            r.get("certified_by") or "",
            r.get("approved_by") or approver or "",
            r.get("approved_at") or approved_at or "",
            r.get("created_at") or "",
        ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Summary" sheetId="1" r:id="rId1"/>'
            '<sheet name="Certifications" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(summary_rows))
        zf.writestr("xl/worksheets/sheet2.xml", _build_sheet_xml(detail_rows))
    return buf.getvalue()

