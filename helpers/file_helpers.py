import os
import re
import uuid
import asyncio
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment
import pandas as pd
import docx
import pdfplumber

APP_URL = os.getenv("APP_URL", "")
FILE_SERVE_DIR = '/var/www/html/bot/static/files'


async def create_keyword_report_file(data: list) -> str:
    file_name = f"{str(uuid.uuid4())[:6]}_keyword_statistics.xlsx"
    file_path = f"{FILE_SERVE_DIR}/{file_name}"

    os.makedirs(FILE_SERVE_DIR, exist_ok=True)

    # Create a new workbook and active worksheet
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Keyword Statistics"

    # Define headers
    headers = [
        "Keyword",
        "Average Monthly Searches",
        "Competition",
        "Competition Index",
        "Low Top of Page Bid (micros)",
        "High Top of Page Bid (micros)"
    ]

    # Write header row with styling
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data rows
    for row_num, result in enumerate(data, 2):
        ws.cell(row=row_num, column=1, value=result.get("keyword", ""))
        ws.cell(row=row_num, column=2, value=result.get("avg_monthly_searches", "N/A"))
        ws.cell(row=row_num, column=3, value=result.get("competition", "N/A"))
        ws.cell(row=row_num, column=4, value=result.get("competition_index", "N/A"))
        ws.cell(row=row_num, column=5, value=result.get("low_top_of_page_bid", "N/A"))
        ws.cell(row=row_num, column=6, value=result.get("high_top_of_page_bid", "N/A"))

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        column = col[0].column  # Get column name
        for cell in col:
            try:
                cell_length = len(str(cell.value))
                if cell_length > max_length:
                    max_length = cell_length
            except Exception:
                pass
        ws.column_dimensions[get_column_letter(column)].width = max_length + 2

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, wb.save, file_path)

    return f"{APP_URL}/downloads/{file_name}", file_path


def create_ads_campaign_file(data: str) -> str:
    file_name = f"{str(uuid.uuid4())[:6]}_ads_campaign_ideas.txt"
    file_path = f"{FILE_SERVE_DIR}/{file_name}"

    with open(file_path, 'w') as f:
        f.write(data)

    return f"{APP_URL}/downloads/{file_name}", file_path


def file_to_text(file_path: str) -> str:
    if file_path.endswith(('.xlsx', '.xls')):
        # Read Excel and convert to CSV text
        df = pd.read_excel(file_path)
        return df.to_csv(index=False)
    
    elif file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
        return df.to_csv(index=False)
    
    elif file_path.endswith('.txt'):
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    elif file_path.endswith('.docx'):
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    
    elif file_path.endswith('.pdf'):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        return text
    
    else:
        raise ValueError("Unsupported file type")
    

def sanitize_text(text: str) -> str:
    # Remove prohibited symbols
    return re.sub(r"[#\$]{2,}", "", text).strip()